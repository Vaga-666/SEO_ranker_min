import json
import re
import traceback
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AnalysisRun, PageFeature, PageScore, PageSnapshot, Recommendation, SerpResult
from app.scoring_config import SCORING_WEIGHTS
from app.services.ai_analyzer import page_analyzer, serp_intent_analyzer
from app.services.codex_advisor import codex_fix_advisor
from app.services.debug_io import append_run_log, save_run_json
from app.services.feature_extractor import extract_page_features
from app.services.gap_analysis import build_basic_gap_recommendations
from app.services.page_fetch import fetch_page_html
from app.services.scoring import score_page_feature, score_page_feature_debug
from app.services.serp_yandex import fetch_yandex_top10, fetch_yandex_top10_interactive
from app.services.target_debug import (
    build_target_html_debug,
    feature_debug_dict,
    log_payload_fields,
    score_debug_dict,
)


def run_analysis_step_serp(db: Session, run: AnalysisRun) -> None:
    append_run_log(run.id, "pipeline.start", query=run.query, target_url=run.target_url)
    run.status = "running"
    db.add(run)
    db.commit()
    append_run_log(run.id, "serp.start", status=run.status)

    run_dir = Path("artifacts") / "runs" / str(run.id)
    try:
        serp_data = fetch_yandex_top10(query=run.query, run_dir=run_dir)
        append_run_log(
            run.id,
            "serp.fetched",
            items_count=len(serp_data.items),
            html_path=serp_data.html_path,
            screenshot_path=serp_data.screenshot_path,
            attempts=serp_data.attempts,
            note=serp_data.note,
        )
    except Exception as exc:
        error_text = _format_exception(exc)
        lowered = error_text.lower()
        if "captcha" in lowered or "robot" in lowered:
            run.status = "captcha_detected"
        else:
            run.status = "failed"
        run.error_message = f"SERP fetch error: {error_text}"
        db.add(run)
        db.commit()
        append_run_log(
            run.id,
            "serp.failed",
            status=run.status,
            error=run.error_message,
            traceback=traceback.format_exc(),
        )
        _save_debug_summary(db=db, run=run)
        return

    db.query(SerpResult).filter(SerpResult.analysis_run_id == run.id).delete()

    for item in serp_data.items:
        is_target = _same_domain(item.url, run.target_url)
        db.add(
            SerpResult(
                analysis_run_id=run.id,
                position=item.position,
                url=item.url,
                title=item.title,
                snippet=item.snippet,
                domain=item.domain,
                is_target=is_target,
            )
        )

    run.status = "serp_collected" if serp_data.items else "no_results"
    if not serp_data.items:
        run.error_message = f"SERP empty: {serp_data.note or 'unknown_reason'}"
    else:
        run.error_message = None
    db.add(run)
    db.commit()
    append_run_log(run.id, "serp.saved", status=run.status, saved_count=len(serp_data.items))
    _save_debug_summary(db=db, run=run)

    if serp_data.items:
        run_analysis_step_pages(db=db, run=run, run_dir=run_dir)


def run_analysis_step_serp_interactive(db: Session, run: AnalysisRun) -> None:
    append_run_log(run.id, "serp.interactive.start", status=run.status)
    run.status = "running_interactive_serp"
    run.error_message = None
    db.add(run)
    db.commit()

    run_dir = Path("artifacts") / "runs" / str(run.id)
    try:
        serp_data = fetch_yandex_top10_interactive(query=run.query, run_dir=run_dir)
        append_run_log(
            run.id,
            "serp.interactive.fetched",
            items_count=len(serp_data.items),
            html_path=serp_data.html_path,
            screenshot_path=serp_data.screenshot_path,
            attempts=serp_data.attempts,
            note=serp_data.note,
        )
    except Exception as exc:
        error_text = _format_exception(exc)
        lowered = error_text.lower()
        if "captcha" in lowered or "robot" in lowered:
            run.status = "captcha_detected"
        else:
            run.status = "failed"
        run.error_message = f"SERP interactive error: {error_text}"
        db.add(run)
        db.commit()
        append_run_log(
            run.id,
            "serp.interactive.failed",
            status=run.status,
            error=run.error_message,
            traceback=traceback.format_exc(),
        )
        _save_debug_summary(db=db, run=run)
        return

    db.query(SerpResult).filter(SerpResult.analysis_run_id == run.id).delete()
    for item in serp_data.items:
        is_target = _same_domain(item.url, run.target_url)
        db.add(
            SerpResult(
                analysis_run_id=run.id,
                position=item.position,
                url=item.url,
                title=item.title,
                snippet=item.snippet,
                domain=item.domain,
                is_target=is_target,
            )
        )

    run.status = "serp_collected_interactive" if serp_data.items else "no_results"
    if not serp_data.items:
        run.error_message = f"SERP empty: {serp_data.note or 'unknown_reason'}"
    else:
        run.error_message = None
    db.add(run)
    db.commit()
    append_run_log(
        run.id,
        "serp.interactive.saved",
        status=run.status,
        saved_count=len(serp_data.items),
    )
    _save_debug_summary(db=db, run=run)

    if serp_data.items:
        run_analysis_step_pages(db=db, run=run, run_dir=run_dir)


def run_analysis_with_manual_serp(db: Session, run: AnalysisRun, manual_urls: list[str]) -> None:
    append_run_log(run.id, "manual_serp.start", manual_urls_count=len(manual_urls))
    run.status = "running_manual_serp"
    run.error_message = None
    db.add(run)
    db.commit()

    run_dir = Path("artifacts") / "runs" / str(run.id)
    run_dir.mkdir(parents=True, exist_ok=True)

    db.query(SerpResult).filter(SerpResult.analysis_run_id == run.id).delete()
    for idx, url in enumerate(manual_urls[:10], start=1):
        domain = urlparse(url).netloc.lower().replace("www.", "")
        db.add(
            SerpResult(
                analysis_run_id=run.id,
                position=idx,
                url=url,
                title=f"Manual URL #{idx}",
                snippet="Imported manually",
                domain=domain,
                is_target=_same_domain(url, run.target_url),
            )
        )

    run.status = "serp_collected_manual"
    db.add(run)
    db.commit()
    append_run_log(run.id, "manual_serp.saved", status=run.status, saved_count=min(len(manual_urls), 10))
    _save_debug_summary(db=db, run=run)

    run_analysis_step_pages(db=db, run=run, run_dir=run_dir)


def run_analysis_step_pages(db: Session, run: AnalysisRun, run_dir: Path) -> None:
    append_run_log(run.id, "pages.start")
    pages_dir = run_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    db.query(PageSnapshot).filter(PageSnapshot.analysis_run_id == run.id).delete()
    db.query(PageFeature).filter(PageFeature.analysis_run_id == run.id).delete()

    serp_results = (
        db.query(SerpResult)
        .filter(SerpResult.analysis_run_id == run.id)
        .order_by(SerpResult.position.asc())
        .all()
    )
    urls_to_process: list[tuple[str, str]] = [
        (item.url, "target" if _urls_match(item.url, run.target_url) else "competitor")
        for item in serp_results
    ]
    if not any(_urls_match(item.url, run.target_url) for item in serp_results):
        urls_to_process.append((run.target_url, "target"))

    had_errors = False
    for index, (url, source_type) in enumerate(urls_to_process, start=1):
        page_path = pages_dir / f"page_{index:02d}.html"
        fetch_result = fetch_page_html(url=url, output_path=page_path)
        append_run_log(
            run.id,
            "pages.fetch",
            index=index,
            url=url,
            source_type=source_type,
            fetch_status=fetch_result.fetch_status,
            http_status=fetch_result.http_status,
            raw_html_path=fetch_result.raw_html_path,
            fetch_method=fetch_result.fetch_method,
            error=fetch_result.error_message,
        )

        db.add(
            PageSnapshot(
                analysis_run_id=run.id,
                source_url=url,
                source_type=source_type,
                http_status=fetch_result.http_status,
                fetch_status=fetch_result.fetch_status,
                raw_html_path=fetch_result.raw_html_path,
                screenshot_path=None,
                error_message=fetch_result.error_message,
            )
        )

        if fetch_result.fetch_status not in {"ok", "http_error"} or not fetch_result.raw_html_path:
            had_errors = True
            continue

        try:
            html = Path(fetch_result.raw_html_path).read_text(encoding="utf-8", errors="ignore")
            features = extract_page_features(html=html, page_url=url)
            if source_type == "target":
                target_debug = build_target_html_debug(
                    target_url=url,
                    fetch_method=fetch_result.fetch_method,
                    html=html,
                    extracted_features=feature_debug_dict(features),
                )
                append_run_log(run.id, "target.html_debug", **log_payload_fields(target_debug))
                save_run_json(run.id, "target_feature_debug.json", target_debug)
            db.add(
                PageFeature(
                    analysis_run_id=run.id,
                    source_url=url,
                    source_type=source_type,
                    title=features.title,
                    meta_description=features.meta_description,
                    h1=features.h1,
                    h2_h3_json=features.h2_h3_json,
                    main_text=features.main_text,
                    has_faq=features.has_faq,
                    has_tables=features.has_tables,
                    has_lists=features.has_lists,
                    has_cta=features.has_cta,
                    has_forms=features.has_forms,
                    has_prices=features.has_prices,
                    has_reviews=features.has_reviews,
                    has_contacts=features.has_contacts,
                    internal_links_count=features.internal_links_count,
                    external_links_count=features.external_links_count,
                    has_schema_org=features.has_schema_org,
                )
            )
            append_run_log(
                run.id,
                "pages.features_extracted",
                url=url,
                source_type=source_type,
                has_faq=features.has_faq,
                has_forms=features.has_forms,
                has_prices=features.has_prices,
                has_reviews=features.has_reviews,
                has_contacts=features.has_contacts,
            )
        except Exception:
            had_errors = True
            append_run_log(
                run.id,
                "pages.features_failed",
                url=url,
                source_type=source_type,
                traceback=traceback.format_exc(),
            )

    run.status = "pages_processed_partial" if had_errors else "pages_processed"
    db.add(run)
    db.commit()
    append_run_log(run.id, "pages.done", status=run.status, had_errors=had_errors)
    _save_debug_summary(db=db, run=run)
    run_analysis_step_scoring(db=db, run=run, had_errors=had_errors)


def run_analysis_step_scoring(db: Session, run: AnalysisRun, had_errors: bool = False) -> None:
    append_run_log(run.id, "scoring.start")
    db.query(PageScore).filter(PageScore.analysis_run_id == run.id).delete()

    features = (
        db.query(PageFeature)
        .filter(PageFeature.analysis_run_id == run.id)
        .order_by(PageFeature.id.asc())
        .all()
    )

    scores: list[PageScore] = []
    for feature in features:
        score = score_page_feature(query=run.query, feature=feature)
        score_debug = score_page_feature_debug(query=run.query, feature=feature)
        score_row = PageScore(
            analysis_run_id=run.id,
            source_url=feature.source_url,
            source_type=feature.source_type,
            intent_fit=score.intent_fit,
            structure_fit=score.structure_fit,
            semantic_coverage=score.semantic_coverage,
            commercial_fit=score.commercial_fit,
            trust_fit=score.trust_fit,
            total_score=score.total_score,
        )
        db.add(score_row)
        scores.append(score_row)
        append_run_log(
            run.id,
            "scoring.page",
            source_url=feature.source_url,
            source_type=feature.source_type,
            total_score=score.total_score,
        )
        if feature.source_type == "target":
            _save_target_feature_debug(db=db, run=run, feature=feature, score=score, score_reasons=score_debug.reasons)

    db.flush()

    sorted_scores = sorted(scores, key=lambda x: x.total_score, reverse=True)
    for rank, row in enumerate(sorted_scores, start=1):
        row.internal_rank = rank
        db.add(row)

    if not scores:
        run.status = "scoring_skipped"
    elif had_errors:
        run.status = "scored_partial"
    else:
        run.status = "scored"
    db.add(run)
    db.commit()
    append_run_log(run.id, "scoring.done", status=run.status, scored_count=len(scores), had_errors=had_errors)
    _save_debug_summary(db=db, run=run)
    run_analysis_step_ai_and_recommendations(db=db, run=run, had_errors=had_errors)


def _save_target_feature_debug(
    *,
    db: Session,
    run: AnalysisRun,
    feature: PageFeature,
    score: Any,
    score_reasons: dict[str, Any],
) -> None:
    snapshot = (
        db.query(PageSnapshot)
        .filter(PageSnapshot.analysis_run_id == run.id, PageSnapshot.source_type == "target")
        .order_by(PageSnapshot.id.desc())
        .first()
    )
    html = ""
    html_path = snapshot.raw_html_path if snapshot else None
    if html_path:
        try:
            html = Path(html_path).read_text(encoding="utf-8", errors="ignore")
        except Exception:
            html = ""
    payload = build_target_html_debug(
        target_url=feature.source_url,
        fetch_method=_infer_fetch_method(snapshot),
        html=html,
        extracted_features=feature_debug_dict(feature),
        calculated_scores=score_debug_dict(score),
        score_reasons=score_reasons,
    )
    append_run_log(run.id, "target.feature_debug_saved", **log_payload_fields(payload))
    save_run_json(run.id, "target_feature_debug.json", payload)


def _infer_fetch_method(snapshot: PageSnapshot | None) -> str:
    if snapshot is None or not snapshot.raw_html_path:
        return "unknown"
    try:
        html = Path(snapshot.raw_html_path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return "unknown"
    return "playwright_rendered" if "data-seo-ranker-rendered" in html else "httpx"


def run_analysis_step_ai_and_recommendations(db: Session, run: AnalysisRun, had_errors: bool = False) -> None:
    append_run_log(run.id, "ai.start")
    cfg = get_settings()
    append_run_log(
        run.id,
        "ai.config",
        base_url=cfg.openai_base_url,
        model=cfg.openai_model,
        key_mask=cfg.openai_key_masked,
        key_is_set=bool(cfg.openai_api_key),
    )
    serp_results = (
        db.query(SerpResult)
        .filter(SerpResult.analysis_run_id == run.id)
        .order_by(SerpResult.position.asc())
        .all()
    )
    features = (
        db.query(PageFeature)
        .filter(PageFeature.analysis_run_id == run.id)
        .order_by(PageFeature.id.asc())
        .all()
    )
    scores = db.query(PageScore).filter(PageScore.analysis_run_id == run.id).all()

    score_map = {_normalize_url(s.source_url): s for s in scores}
    feature_map = {_normalize_url(f.source_url): f for f in features}

    top_results_short = [
        {"position": row.position, "url": row.url, "title": row.title, "snippet": row.snippet}
        for row in serp_results
    ]
    page_features_short = [
        {
            "url": f.source_url,
            "h1": f.h1,
            "has_faq": f.has_faq,
            "has_forms": f.has_forms,
            "has_prices": f.has_prices,
            "has_reviews": f.has_reviews,
            "has_contacts": f.has_contacts,
        }
        for f in features
    ]

    serp_summary = serp_intent_analyzer(
        query=run.query,
        top_results=top_results_short,
        page_features_short=page_features_short,
        run_id=run.id,
    )
    ai_used = serp_summary is not None
    append_run_log(run.id, "ai.serp_summary", ai_used=ai_used, has_summary=serp_summary is not None)

    ai_recommendations: list[Recommendation] = []
    if serp_summary is None:
        serp_summary = _build_basic_serp_summary(features)
    _save_serp_summary_artifact(run_id=run.id, summary=serp_summary, ai_used=ai_used)

    for feature in features:
        ai_result = page_analyzer(
            query=run.query,
            serp_summary=serp_summary,
            feature=feature,
            run_id=run.id,
        )
        if not ai_result:
            append_run_log(run.id, "ai.page_skipped", source_url=feature.source_url)
            continue
        ai_used = True
        append_run_log(run.id, "ai.page_done", source_url=feature.source_url)

        score_row = score_map.get(_normalize_url(feature.source_url))
        if score_row is None:
            continue

        score_row.intent_fit = _clamp_0_100(ai_result.get("intent_fit_score", score_row.intent_fit))
        score_row.structure_fit = _clamp_0_100(ai_result.get("structure_fit_score", score_row.structure_fit))
        score_row.semantic_coverage = _clamp_0_100(ai_result.get("semantic_score", score_row.semantic_coverage))
        score_row.commercial_fit = _clamp_0_100(ai_result.get("commercial_score", score_row.commercial_fit))
        score_row.trust_fit = _clamp_0_100(ai_result.get("trust_score", score_row.trust_fit))
        score_row.total_score = round(
            score_row.intent_fit * SCORING_WEIGHTS["intent_fit"]
            + score_row.structure_fit * SCORING_WEIGHTS["structure_fit"]
            + score_row.semantic_coverage * SCORING_WEIGHTS["semantic_coverage"]
            + score_row.commercial_fit * SCORING_WEIGHTS["commercial_fit"]
            + score_row.trust_fit * SCORING_WEIGHTS["trust_fit"],
            2,
        )
        db.add(score_row)

        for text in ai_result.get("recommendations_short", [])[:5]:
            if not isinstance(text, str) or not text.strip():
                continue
            ai_recommendations.append(
                Recommendation(
                    analysis_run_id=run.id,
                    source_url=feature.source_url,
                    priority="medium",
                    text=text.strip(),
                )
            )

    db.flush()

    rescored = db.query(PageScore).filter(PageScore.analysis_run_id == run.id).all()
    for rank, row in enumerate(sorted(rescored, key=lambda s: s.total_score, reverse=True), start=1):
        row.internal_rank = rank
        db.add(row)

    db.query(Recommendation).filter(Recommendation.analysis_run_id == run.id).delete()
    for rec in ai_recommendations[:15]:
        db.add(rec)

    target_feature = feature_map.get(_normalize_url(run.target_url))
    target_score = score_map.get(_normalize_url(run.target_url))
    top_features = [feature_map.get(_normalize_url(row.url)) for row in serp_results]
    top_features = [f for f in top_features if f is not None]
    top_scores = [score_map.get(_normalize_url(row.url)) for row in serp_results]
    top_scores = [s for s in top_scores if s is not None]

    basic_recs = build_basic_gap_recommendations(
        run_id=run.id,
        target_feature=target_feature,
        target_score=target_score,
        top_features=top_features,
        top_scores=top_scores,
    )
    for rec in basic_recs:
        db.add(rec)

    if ai_used and had_errors:
        run.status = "done_ai_partial"
    elif ai_used:
        run.status = "done_ai"
    elif had_errors:
        run.status = "done_no_ai_partial"
    else:
        run.status = "done_no_ai"
    db.add(run)
    db.commit()
    append_run_log(
        run.id,
        "pipeline.done",
        status=run.status,
        ai_used=ai_used,
        recommendations_saved=min(len(ai_recommendations), 15) + len(basic_recs),
    )
    _save_debug_summary(db=db, run=run)


def _same_domain(url_a: str, url_b: str) -> bool:
    host_a = urlparse(url_a).netloc.lower().replace("www.", "")
    host_b = urlparse(url_b).netloc.lower().replace("www.", "")
    return bool(host_a and host_b and host_a == host_b)


def _urls_match(url_a: str, url_b: str) -> bool:
    return url_a.rstrip("/") == url_b.rstrip("/")


def _normalize_url(url: str) -> str:
    return url.rstrip("/")


def _clamp_0_100(value: object) -> float:
    try:
        val = float(value)
    except Exception:
        return 0.0
    return max(0.0, min(100.0, round(val, 2)))


def _save_serp_summary_artifact(run_id: int, summary: dict, ai_used: bool) -> None:
    run_dir = Path("artifacts") / "runs" / str(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": "ai" if ai_used else "basic_fallback",
        "summary": summary,
    }
    (run_dir / "serp_summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    append_run_log(run_id, "artifact.serp_summary_saved", source=payload["source"])


def _format_exception(exc: Exception) -> str:
    # Some Playwright errors stringify to an empty string; always return useful context.
    message = str(exc).strip()
    if message:
        return f"{exc.__class__.__name__}: {message}"
    repr_text = repr(exc).strip()
    if repr_text:
        return f"{exc.__class__.__name__}: {repr_text}"
    return exc.__class__.__name__


def _save_debug_summary(db: Session, run: AnalysisRun) -> None:
    serp_count = db.query(SerpResult).filter(SerpResult.analysis_run_id == run.id).count()
    snapshot_count = db.query(PageSnapshot).filter(PageSnapshot.analysis_run_id == run.id).count()
    feature_count = db.query(PageFeature).filter(PageFeature.analysis_run_id == run.id).count()
    score_count = db.query(PageScore).filter(PageScore.analysis_run_id == run.id).count()
    rec_count = db.query(Recommendation).filter(Recommendation.analysis_run_id == run.id).count()
    payload = {
        "run_id": run.id,
        "query": run.query,
        "target_url": run.target_url,
        "status": run.status,
        "error_message": run.error_message,
        "counts": {
            "serp_results": serp_count,
            "page_snapshots": snapshot_count,
            "page_features": feature_count,
            "page_scores": score_count,
            "recommendations": rec_count,
        },
    }
    save_run_json(run.id, "debug_result_summary.json", payload)
    _save_codex_advice(db=db, run=run, summary_payload=payload)


def _save_codex_advice(db: Session, run: AnalysisRun, summary_payload: dict) -> None:
    final_statuses = {
        "failed",
        "captcha_detected",
        "no_results",
        "done_no_ai",
        "done_no_ai_partial",
        "done_ai",
        "done_ai_partial",
        "scoring_skipped",
    }
    if run.status not in final_statuses:
        return

    context = _build_codex_context(db=db, run=run, summary_payload=summary_payload)
    advice = codex_fix_advisor(context)
    save_run_json(run.id, "codex_advice.json", advice)
    append_run_log(run.id, "artifact.codex_advice_saved", has_items=bool(advice.get("priority_fixes")))


def _build_codex_context(db: Session, run: AnalysisRun, summary_payload: dict) -> dict:
    serp_results = (
        db.query(SerpResult)
        .filter(SerpResult.analysis_run_id == run.id)
        .order_by(SerpResult.position.asc())
        .all()
    )
    features = (
        db.query(PageFeature)
        .filter(PageFeature.analysis_run_id == run.id)
        .order_by(PageFeature.id.asc())
        .all()
    )
    scores = (
        db.query(PageScore)
        .filter(PageScore.analysis_run_id == run.id)
        .order_by(PageScore.internal_rank.asc().nulls_last(), PageScore.id.asc())
        .all()
    )
    recommendations = (
        db.query(Recommendation)
        .filter(Recommendation.analysis_run_id == run.id)
        .order_by(Recommendation.id.asc())
        .all()
    )

    score_map = {_normalize_url(s.source_url): s for s in scores}
    feature_map = {_normalize_url(f.source_url): f for f in features}

    top_scores = [score_map.get(_normalize_url(item.url)) for item in serp_results]
    top_scores = [s for s in top_scores if s is not None]
    target_score = score_map.get(_normalize_url(run.target_url))
    top_avg = _calc_top_avg_scores(top_scores)
    target_vs_top = _build_target_vs_top_payload(target_score=target_score, top_avg=top_avg)

    target_feature = feature_map.get(_normalize_url(run.target_url))
    top_features = [feature_map.get(_normalize_url(item.url)) for item in serp_results]
    top_features = [f for f in top_features if f is not None]
    gap_report = _build_gap_report_for_codex(
        target_feature=target_feature,
        top_features=top_features,
        target_vs_top=target_vs_top,
    )

    serp_summary_payload = _read_serp_summary_payload(run.id)
    top_competitors = []
    for item in serp_results[:10]:
        competitor_score = score_map.get(_normalize_url(item.url))
        top_competitors.append(
            {
                "position": item.position,
                "url": item.url,
                "title": item.title,
                "total_score": round(competitor_score.total_score, 2) if competitor_score else None,
            }
        )

    grouped_recommendations = {
        "urgent": [r.text for r in recommendations if r.priority == "urgent"][:10],
        "medium": [r.text for r in recommendations if r.priority == "medium"][:10],
        "later": [r.text for r in recommendations if r.priority == "later"][:10],
    }

    return {
        "run_id": run.id,
        "query": run.query,
        "target_url": run.target_url,
        "status": run.status,
        "error_message": run.error_message,
        "counts": summary_payload.get("counts", {}),
        "target_vs_top": target_vs_top,
        "gap_report": gap_report,
        "top_competitors": top_competitors,
        "serp_summary": serp_summary_payload,
        "recommendations": grouped_recommendations,
    }


def _calc_top_avg_scores(rows: list[PageScore]) -> dict[str, float] | None:
    if not rows:
        return None
    total = max(1, len(rows))
    return {
        "intent_fit": round(sum(r.intent_fit for r in rows) / total, 2),
        "structure_fit": round(sum(r.structure_fit for r in rows) / total, 2),
        "semantic_coverage": round(sum(r.semantic_coverage for r in rows) / total, 2),
        "commercial_fit": round(sum(r.commercial_fit for r in rows) / total, 2),
        "trust_fit": round(sum(r.trust_fit for r in rows) / total, 2),
        "total_score": round(sum(r.total_score for r in rows) / total, 2),
    }


def _build_target_vs_top_payload(
    target_score: PageScore | None,
    top_avg: dict[str, float] | None,
) -> dict[str, dict[str, float]]:
    metrics = ("intent_fit", "structure_fit", "semantic_coverage", "commercial_fit", "trust_fit", "total_score")
    if not target_score or not top_avg:
        return {name: {"target": 0.0, "top_avg": 0.0, "diff": 0.0} for name in metrics}

    payload: dict[str, dict[str, float]] = {}
    for name in metrics:
        target_val = round(float(getattr(target_score, name, 0.0) or 0.0), 2)
        top_val = round(float(top_avg.get(name, 0.0) or 0.0), 2)
        payload[name] = {
            "target": target_val,
            "top_avg": top_val,
            "diff": round(target_val - top_val, 2),
        }
    return payload


def _build_gap_report_for_codex(
    target_feature: PageFeature | None,
    top_features: list[PageFeature],
    target_vs_top: dict[str, dict[str, float]] | None,
) -> dict[str, list[str]]:
    if not target_feature or not top_features:
        return {
            "missing_blocks": [],
            "missing_entities": [],
            "commercial_gaps": [],
            "trust_gaps": [],
        }

    block_defs = [
        ("has_faq", "FAQ"),
        ("has_tables", "Таблицы"),
        ("has_lists", "Списки"),
        ("has_cta", "CTA-блок"),
        ("has_forms", "Формы заявки"),
        ("has_prices", "Блок цен"),
        ("has_reviews", "Отзывы/кейсы"),
        ("has_contacts", "Контакты"),
        ("has_schema_org", "Schema.org"),
    ]

    missing_blocks: list[str] = []
    for attr, title in block_defs:
        top_ratio = sum(1 for f in top_features if bool(getattr(f, attr))) / max(1, len(top_features))
        if top_ratio >= 0.5 and not bool(getattr(target_feature, attr)):
            missing_blocks.append(f"{title} (есть у {round(top_ratio * 100)}% top-страниц)")

    target_entities = set(
        _tokenize_for_codex(" ".join([target_feature.title or "", target_feature.h1 or "", target_feature.meta_description or ""]))
    )
    top_entities: set[str] = set()
    for feature in top_features:
        top_entities.update(
            _tokenize_for_codex(" ".join([feature.title or "", feature.h1 or "", feature.meta_description or ""]))
        )
    missing_entities = sorted([ent for ent in top_entities if ent not in target_entities])[:12]

    commercial_gaps: list[str] = []
    trust_gaps: list[str] = []
    if target_vs_top:
        c_diff = float((target_vs_top.get("commercial_fit") or {}).get("diff", 0.0))
        s_diff = float((target_vs_top.get("semantic_coverage") or {}).get("diff", 0.0))
        t_diff = float((target_vs_top.get("trust_fit") or {}).get("diff", 0.0))
        if c_diff < -5:
            commercial_gaps.append(f"Commercial fit ниже среднего top-10 на {abs(round(c_diff, 2))}")
        if s_diff < -8:
            commercial_gaps.append("Слабее покрытие интента и коммерческих подтем")
        if t_diff < -5:
            trust_gaps.append(f"Trust fit ниже среднего top-10 на {abs(round(t_diff, 2))}")

    if not target_feature.has_forms:
        commercial_gaps.append("Нет формы заявки/обратной связи")
    if not target_feature.has_prices:
        commercial_gaps.append("Нет явных цен/тарифов")
    if not target_feature.has_cta:
        commercial_gaps.append("Нет выраженного CTA")

    if not target_feature.has_reviews:
        trust_gaps.append("Нет отзывов/кейсов")
    if not target_feature.has_contacts:
        trust_gaps.append("Недостаточно контактной информации")
    if not target_feature.has_schema_org:
        trust_gaps.append("Нет schema.org-разметки")
    if target_feature.external_links_count == 0:
        trust_gaps.append("Нет внешних ссылок на подтверждающие источники")

    return {
        "missing_blocks": _dedupe_keep_order(missing_blocks),
        "missing_entities": _dedupe_keep_order(missing_entities),
        "commercial_gaps": _dedupe_keep_order(commercial_gaps),
        "trust_gaps": _dedupe_keep_order(trust_gaps),
    }


def _read_serp_summary_payload(run_id: int) -> dict[str, Any]:
    path = Path("artifacts") / "runs" / str(run_id) / "serp_summary.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _tokenize_for_codex(text: str) -> list[str]:
    raw = re.findall(r"[a-zA-Zа-яА-Я0-9]+", (text or "").lower())
    stop = {
        "для",
        "как",
        "что",
        "это",
        "или",
        "при",
        "best",
        "online",
        "learning",
        "platform",
        "platforms",
        "review",
        "blog",
        "article",
    }
    out: list[str] = []
    for token in raw:
        if token in stop:
            continue
        if token.isdigit() or re.fullmatch(r"\d{4}", token):
            continue
        if len(token) < 4:
            continue
        out.append(token)
    return out


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _build_basic_serp_summary(features: list[PageFeature]) -> dict:
    competitors = [f for f in features if f.source_type == "competitor"]
    if not competitors:
        competitors = features
    if not competitors:
        return {
            "dominant_intent": "unknown",
            "dominant_page_type": "unknown",
            "required_blocks": [],
            "common_entities": [],
            "commercial_expectations": [],
            "trust_expectations": [],
        }

    def ratio(predicate) -> float:
        return sum(1 for f in competitors if predicate(f)) / max(1, len(competitors))

    block_defs = [
        ("FAQ", lambda f: f.has_faq),
        ("Таблицы", lambda f: f.has_tables),
        ("Списки", lambda f: f.has_lists),
        ("CTA", lambda f: f.has_cta),
        ("Формы", lambda f: f.has_forms),
        ("Цены", lambda f: f.has_prices),
        ("Отзывы", lambda f: f.has_reviews),
        ("Контакты", lambda f: f.has_contacts),
        ("Schema.org", lambda f: f.has_schema_org),
    ]
    required_blocks = [name for name, fn in block_defs if ratio(fn) >= 0.5]

    title_text = " ".join([(f.title or "") + " " + (f.h1 or "") for f in competitors]).lower()
    listicle_markers = ["топ", "лучш", "обзор", "рейтинг", "best", "top", "review"]
    listicle_hits = sum(title_text.count(m) for m in listicle_markers)
    dominant_page_type = "listicle_comparison" if listicle_hits >= 3 else "article_or_landing"

    commercial_signal = ratio(lambda f: f.has_forms) + ratio(lambda f: f.has_prices) + ratio(lambda f: f.has_cta)
    dominant_intent = "commercial_comparison" if commercial_signal >= 1.2 else "informational_comparison"

    common_entities = _collect_common_entities(competitors, limit=12)

    commercial_expectations = []
    if ratio(lambda f: f.has_prices) >= 0.5:
        commercial_expectations.append("Показ цен/тарифов")
    if ratio(lambda f: f.has_forms) >= 0.5:
        commercial_expectations.append("Наличие формы заявки")
    if ratio(lambda f: f.has_cta) >= 0.5:
        commercial_expectations.append("Явный CTA-блок")

    trust_expectations = []
    if ratio(lambda f: f.has_reviews) >= 0.5:
        trust_expectations.append("Отзывы/кейсы")
    if ratio(lambda f: f.has_contacts) >= 0.5:
        trust_expectations.append("Контактная информация")
    if ratio(lambda f: f.has_schema_org) >= 0.4:
        trust_expectations.append("Schema.org-разметка")

    return {
        "dominant_intent": dominant_intent,
        "dominant_page_type": dominant_page_type,
        "required_blocks": required_blocks,
        "common_entities": common_entities,
        "commercial_expectations": commercial_expectations,
        "trust_expectations": trust_expectations,
    }


def _collect_common_entities(features: list[PageFeature], limit: int = 12) -> list[str]:
    tokens: list[str] = []
    for f in features:
        source = " ".join([f.title or "", f.h1 or "", f.meta_description or ""])
        tokens.extend(_tokenize(source))
    counts = Counter(tokens)
    return [word for word, _ in counts.most_common(limit)]


def _tokenize(text: str) -> list[str]:
    raw = re.findall(r"[a-zA-Zа-яА-Я0-9]+", (text or "").lower())
    stop = {
        "для",
        "как",
        "что",
        "это",
        "или",
        "при",
        "best",
        "online",
        "learning",
        "platform",
        "platforms",
        "review",
        "blog",
        "article",
    }
    out: list[str] = []
    for t in raw:
        if t in stop:
            continue
        if t.isdigit() or re.fullmatch(r"\d{4}", t):
            continue
        if len(t) < 4:
            continue
        out.append(t)
    return out
