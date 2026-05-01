import json
import re
from pathlib import Path
import sys
import threading
import uuid
from datetime import datetime

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import Base, SessionLocal, engine, get_db
from app.models import AnalysisRun, KeywordEntry, PageFeature, PageScore, PageSnapshot, Recommendation, SerpResult
from app.services.pipeline import (
    run_analysis_step_ai_and_recommendations,
    run_analysis_step_serp,
    run_analysis_step_serp_interactive,
    run_analysis_with_manual_serp,
)
from app.services.keyword_research import KeywordDiscoveryError, discover_keywords

app = FastAPI(title="SEO Ranker MVP")
templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.on_event("startup")
def startup() -> None:
    Base.metadata.create_all(bind=engine)


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"page_title": "SEO Ranker MVP"},
    )


@app.post("/analyze")
def start_analysis(
    request: Request,
    query: str = Form(...),
    target_url: str = Form(...),
    manual_top_urls: str = Form(""),
    db: Session = Depends(get_db),
):
    run = AnalysisRun(query=query.strip(), target_url=target_url.strip(), status="created")
    db.add(run)
    db.commit()
    db.refresh(run)

    manual_urls = _parse_manual_urls(manual_top_urls)
    thread = threading.Thread(
        target=_run_analysis_background,
        args=(run.id, manual_urls),
        daemon=True,
    )
    thread.start()

    return RedirectResponse(
        url=request.url_for("analysis_detail", run_id=run.id),
        status_code=303,
    )


@app.post("/runs/{run_id}/retry-captcha")
def retry_captcha(run_id: int, request: Request, db: Session = Depends(get_db)):
    run = db.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    run.status = "running_interactive_serp"
    run.error_message = "Идет интерактивный режим: пройдите капчу в браузере..."
    db.add(run)
    db.commit()

    thread = threading.Thread(target=_run_interactive_retry, args=(run.id,), daemon=True)
    thread.start()

    return RedirectResponse(
        url=request.url_for("analysis_detail", run_id=run.id),
        status_code=303,
    )


@app.post("/analyze-start")
def start_analysis_json(
    query: str = Form(...),
    target_url: str = Form(...),
    manual_top_urls: str = Form(""),
    db: Session = Depends(get_db),
):
    run = AnalysisRun(query=query.strip(), target_url=target_url.strip(), status="created")
    db.add(run)
    db.commit()
    db.refresh(run)

    manual_urls = _parse_manual_urls(manual_top_urls)
    thread = threading.Thread(
        target=_run_analysis_background,
        args=(run.id, manual_urls),
        daemon=True,
    )
    thread.start()

    return JSONResponse(
        {
            "run_id": run.id,
            "status_url": f"/runs/{run.id}/progress",
            "detail_url": f"/runs/{run.id}",
        }
    )


@app.post("/keywords/discover")
def keywords_discover(
    target_url: str = Form(""),
    topic: str = Form(""),
):
    target = (target_url or "").strip()
    topic_clean = (topic or "").strip()

    if not topic_clean and not target:
        raise HTTPException(status_code=400, detail="Укажите тему вручную или заполните target URL.")
    if target and not target.startswith("http://") and not target.startswith("https://"):
        raise HTTPException(status_code=400, detail="target_url must start with http:// or https://")

    try:
        result = discover_keywords(
            target_url=target,
            topic_hint=topic_clean,
            request_id=uuid.uuid4().hex,
        )
    except KeywordDiscoveryError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return JSONResponse(result)


@app.post("/keywords/save")
def keywords_save(
    keywords_json: str = Form(...),
    topic: str = Form(""),
    target_url: str = Form(""),
    db: Session = Depends(get_db),
):
    try:
        payload = json.loads(keywords_json)
    except Exception:
        raise HTTPException(status_code=400, detail="Некорректный JSON в keywords_json.")
    if not isinstance(payload, list):
        raise HTTPException(status_code=400, detail="keywords_json должен быть массивом.")

    topic_clean = (topic or "").strip()[:512]
    target_clean = (target_url or "").strip()[:2048]
    saved = 0
    skipped = 0

    for item in payload:
        if not isinstance(item, dict):
            skipped += 1
            continue
        phrase = str(item.get("phrase") or "").strip()
        if not phrase:
            skipped += 1
            continue
        count_val = item.get("count")
        freq = count_val if isinstance(count_val, int) else None

        exists = db.query(KeywordEntry).filter(KeywordEntry.phrase == phrase).first()
        if exists:
            if isinstance(freq, int) and (exists.frequency is None or freq > exists.frequency):
                exists.frequency = freq
            if topic_clean and not exists.topic:
                exists.topic = topic_clean
            if target_clean and not exists.target_url:
                exists.target_url = target_clean
            db.add(exists)
            skipped += 1
            continue

        db.add(
            KeywordEntry(
                phrase=phrase,
                frequency=freq,
                topic=topic_clean or None,
                target_url=target_clean or None,
                source="wordstat",
            )
        )
        saved += 1

    db.commit()
    return JSONResponse({"saved": saved, "skipped": skipped})


@app.post("/runs/{run_id}/rerun-ai-codex")
def rerun_ai_codex(run_id: int, request: Request, db: Session = Depends(get_db)):
    run = db.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    running_statuses = {
        "created",
        "running",
        "running_manual_serp",
        "running_interactive_serp",
        "serp_collected",
        "serp_collected_manual",
        "serp_collected_interactive",
        "pages_processed",
        "pages_processed_partial",
        "scored",
        "scored_partial",
        "running_ai_codex_manual",
    }
    if run.status in running_statuses:
        return RedirectResponse(
            url=request.url_for("analysis_detail", run_id=run.id),
            status_code=303,
        )

    run.status = "running_ai_codex_manual"
    run.error_message = "Запущен ручной AI-анализ и пересборка рекомендаций для Codex..."
    db.add(run)
    db.commit()

    thread = threading.Thread(target=_run_ai_codex_background, args=(run.id,), daemon=True)
    thread.start()

    return RedirectResponse(
        url=request.url_for("analysis_detail", run_id=run.id),
        status_code=303,
    )


@app.get("/runs/{run_id}", name="analysis_detail")
def analysis_detail(run_id: int, request: Request, db: Session = Depends(get_db)):
    run = db.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    serp_results = (
        db.query(SerpResult)
        .filter(SerpResult.analysis_run_id == run.id)
        .order_by(SerpResult.position.asc())
        .all()
    )
    snapshots = (
        db.query(PageSnapshot)
        .filter(PageSnapshot.analysis_run_id == run.id)
        .order_by(PageSnapshot.id.asc())
        .all()
    )
    page_features = db.query(PageFeature).filter(PageFeature.analysis_run_id == run.id).all()
    features_count = len(page_features)

    page_scores = db.query(PageScore).filter(PageScore.analysis_run_id == run.id).all()
    recommendations = (
        db.query(Recommendation)
        .filter(Recommendation.analysis_run_id == run.id)
        .order_by(Recommendation.id.asc())
        .all()
    )
    score_map = {row.source_url.rstrip("/"): row for row in page_scores}
    feature_map = {row.source_url.rstrip("/"): row for row in page_features}

    top_rows = []
    for item in serp_results:
        score_row = score_map.get(item.url.rstrip("/"))
        top_rows.append(
            {
                "real_position": item.position,
                "our_position": score_row.internal_rank if score_row else None,
                "url": item.url,
                "title": item.title,
                "total_score": round(score_row.total_score, 2) if score_row else None,
            }
        )

    target_score = score_map.get(run.target_url.rstrip("/"))
    top_score_rows = [score_map.get(item.url.rstrip("/")) for item in serp_results]
    top_score_rows = [row for row in top_score_rows if row is not None]
    top_avg = _calc_avg_scores(top_score_rows)

    target_vs_top = None
    if target_score and top_avg:
        target_vs_top = {
            "intent_fit": (target_score.intent_fit, top_avg["intent_fit"]),
            "structure_fit": (target_score.structure_fit, top_avg["structure_fit"]),
            "semantic_coverage": (target_score.semantic_coverage, top_avg["semantic_coverage"]),
            "commercial_fit": (target_score.commercial_fit, top_avg["commercial_fit"]),
            "trust_fit": (target_score.trust_fit, top_avg["trust_fit"]),
            "total_score": (target_score.total_score, top_avg["total_score"]),
        }

    target_feature = feature_map.get(run.target_url.rstrip("/"))
    top_feature_rows = [feature_map.get(item.url.rstrip("/")) for item in serp_results]
    top_feature_rows = [row for row in top_feature_rows if row is not None]
    gap_report = _build_gap_report(
        target_feature=target_feature,
        top_features=top_feature_rows,
        target_vs_top=target_vs_top,
    )
    serp_summary_data = _read_serp_summary(run.id)
    codex_advice = _read_codex_advice(run.id)
    target_feature_debug = _read_target_feature_debug(run.id)

    recommendations_by_priority = {
        "urgent": [r for r in recommendations if r.priority == "urgent"],
        "medium": [r for r in recommendations if r.priority == "medium"],
        "later": [r for r in recommendations if r.priority == "later"],
    }
    running_statuses = {
        "created",
        "running",
        "running_manual_serp",
        "running_interactive_serp",
        "running_ai_codex_manual",
        "serp_collected",
        "serp_collected_manual",
        "serp_collected_interactive",
        "pages_processed",
        "pages_processed_partial",
        "scored",
        "scored_partial",
    }
    is_running = run.status in running_statuses
    running_seconds = int((datetime.utcnow() - run.created_at).total_seconds()) if is_running else 0
    status_hints = {
        "running_interactive_serp": "Открыто окно браузера, пройдите капчу и дождитесь завершения.",
        "running_ai_codex_manual": "Выполняем повторный AI-анализ и формируем рекомендации для Codex.",
        "running": "Идет сбор SERP.",
        "running_manual_serp": "Применяется ручной список top-10.",
        "pages_processed": "Идет расчет score и рекомендаций.",
        "pages_processed_partial": "Часть страниц обработана, продолжаем анализ по доступным данным.",
        "scored": "Идет AI-анализ и финальные рекомендации.",
        "scored_partial": "Идет AI-анализ для доступных обработанных страниц.",
    }

    return templates.TemplateResponse(
        request=request,
        name="run_detail.html",
        context={
            "page_title": f"Run #{run.id}",
            "run": run,
            "serp_results": serp_results,
            "snapshots": snapshots,
            "features_count": features_count,
            "top_rows": top_rows,
            "target_vs_top": target_vs_top,
            "gap_report": gap_report,
            "serp_summary_data": serp_summary_data,
            "codex_advice": codex_advice,
            "target_feature_debug": target_feature_debug,
            "recommendations_by_priority": recommendations_by_priority,
            "is_captcha": run.status == "captcha_detected",
            "is_running": is_running,
            "running_seconds": running_seconds,
            "status_hint": status_hints.get(run.status),
        },
    )


@app.get("/runs/{run_id}/progress")
def run_progress(run_id: int, db: Session = Depends(get_db)):
    run = db.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    percent, message = _status_progress(run.status)
    done_statuses = {
        "done_ai",
        "done_ai_partial",
        "done_no_ai",
        "done_no_ai_partial",
        "failed",
        "captcha_detected",
        "no_results",
        "scoring_skipped",
    }

    return JSONResponse(
        {
            "run_id": run.id,
            "status": run.status,
            "percent": percent,
            "message": message,
            "is_done": run.status in done_statuses,
            "has_error": bool(run.error_message),
            "error_message": run.error_message,
            "detail_url": f"/runs/{run.id}",
        }
    )


def _calc_avg_scores(rows: list[PageScore]) -> dict[str, float] | None:
    if not rows:
        return None

    total = len(rows)
    return {
        "intent_fit": round(sum(r.intent_fit for r in rows) / total, 2),
        "structure_fit": round(sum(r.structure_fit for r in rows) / total, 2),
        "semantic_coverage": round(sum(r.semantic_coverage for r in rows) / total, 2),
        "commercial_fit": round(sum(r.commercial_fit for r in rows) / total, 2),
        "trust_fit": round(sum(r.trust_fit for r in rows) / total, 2),
        "total_score": round(sum(r.total_score for r in rows) / total, 2),
    }


def _parse_manual_urls(raw: str) -> list[str]:
    urls: list[str] = []
    for line in (raw or "").splitlines():
        url = line.strip()
        if not url:
            continue
        if url.startswith("http://") or url.startswith("https://"):
            urls.append(url)

    deduplicated: list[str] = []
    seen: set[str] = set()
    for url in urls:
        key = url.rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(url)

    return deduplicated[:10]


def _build_gap_report(
    target_feature: PageFeature | None,
    top_features: list[PageFeature],
    target_vs_top: dict[str, tuple[float, float]] | None,
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
        target_has = bool(getattr(target_feature, attr))
        if top_ratio >= 0.5 and not target_has:
            missing_blocks.append(f"{title} (есть у {round(top_ratio * 100)}% top-страниц)")

    target_entities = _extract_entities(target_feature)
    top_entities: set[str] = set()
    for feature in top_features:
        top_entities.update(_extract_entities(feature))
    missing_entities = sorted([ent for ent in top_entities if ent not in target_entities])[:12]

    commercial_gaps: list[str] = []
    if target_vs_top:
        commercial_diff = target_vs_top["commercial_fit"][0] - target_vs_top["commercial_fit"][1]
        if commercial_diff < -5:
            commercial_gaps.append(f"Commercial fit ниже среднего top-10 на {abs(round(commercial_diff, 2))}")
        if target_vs_top["semantic_coverage"][0] < target_vs_top["semantic_coverage"][1] - 8:
            commercial_gaps.append("Слабее покрытие интента и коммерческих подтем")

    if not target_feature.has_forms:
        commercial_gaps.append("Нет формы заявки/обратной связи")
    if not target_feature.has_prices:
        commercial_gaps.append("Нет явных цен/тарифов")
    if not target_feature.has_cta:
        commercial_gaps.append("Нет выраженного CTA")

    trust_gaps: list[str] = []
    if target_vs_top:
        trust_diff = target_vs_top["trust_fit"][0] - target_vs_top["trust_fit"][1]
        if trust_diff < -5:
            trust_gaps.append(f"Trust fit ниже среднего top-10 на {abs(round(trust_diff, 2))}")
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


def _extract_entities(feature: PageFeature) -> set[str]:
    entities: set[str] = set()
    entities.update(_tokens(feature.title or ""))
    entities.update(_tokens(feature.h1 or ""))
    entities.update(_tokens(feature.meta_description or ""))
    for heading in _parse_h2_h3(feature.h2_h3_json):
        entities.update(_tokens(heading))
    return {t for t in entities if len(t) > 3}


def _parse_h2_h3(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
        if isinstance(value, list):
            return [str(v) for v in value if isinstance(v, str)]
    except Exception:
        return []
    return []


def _tokens(text: str) -> list[str]:
    stop_words = {
        "для",
        "как",
        "что",
        "это",
        "или",
        "если",
        "при",
        "из",
        "под",
        "the",
        "and",
        "with",
        "from",
        "your",
        "our",
        "best",
        "online",
        "learning",
        "platform",
        "platforms",
        "according",
        "academy",
        "blog",
        "article",
    }
    all_tokens = re.findall(r"[a-zA-Zа-яА-Я0-9]+", text.lower())
    filtered: list[str] = []
    for token in all_tokens:
        if token in stop_words:
            continue
        if token.isdigit():
            continue
        if re.fullmatch(r"\d{4}", token):
            # Usually years from list pages; noisy for gap entities.
            continue
        if len(token) < 4:
            continue
        filtered.append(token)
    return filtered


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _read_serp_summary(run_id: int) -> dict | None:
    path = Path("artifacts") / "runs" / str(run_id) / "serp_summary.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_codex_advice(run_id: int) -> dict | None:
    path = Path("artifacts") / "runs" / str(run_id) / "codex_advice.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_target_feature_debug(run_id: int) -> dict | None:
    path = Path("artifacts") / "runs" / str(run_id) / "target_feature_debug.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _run_interactive_retry(run_id: int) -> None:
    db = SessionLocal()
    try:
        run = db.get(AnalysisRun, run_id)
        if run is None:
            return
        run_analysis_step_serp_interactive(db=db, run=run)
    finally:
        db.close()


def _run_analysis_background(run_id: int, manual_urls: list[str] | None = None) -> None:
    db = SessionLocal()
    try:
        run = db.get(AnalysisRun, run_id)
        if run is None:
            return
        if manual_urls:
            run_analysis_with_manual_serp(db=db, run=run, manual_urls=manual_urls)
        else:
            run_analysis_step_serp(db=db, run=run)
    finally:
        db.close()


def _run_ai_codex_background(run_id: int) -> None:
    db = SessionLocal()
    try:
        run = db.get(AnalysisRun, run_id)
        if run is None:
            return
        snapshots = db.query(PageSnapshot).filter(PageSnapshot.analysis_run_id == run.id).all()
        had_errors = any(s.fetch_status not in {"ok", "http_error"} for s in snapshots)
        run_analysis_step_ai_and_recommendations(db=db, run=run, had_errors=had_errors)
    finally:
        db.close()


def _status_progress(status: str) -> tuple[int, str]:
    mapping: dict[str, tuple[int, str]] = {
        "created": (3, "Создаем задачу..."),
        "running": (15, "Собираем SERP..."),
        "running_manual_serp": (15, "Применяем ручной top-10..."),
        "running_interactive_serp": (15, "Ожидаем прохождение капчи..."),
        "running_ai_codex_manual": (88, "Пересчитываем AI-анализ и рекомендации для Codex..."),
        "serp_collected": (35, "SERP собран, начинаем обработку страниц..."),
        "serp_collected_manual": (35, "Top-10 импортирован, начинаем обработку страниц..."),
        "serp_collected_interactive": (35, "SERP собран после капчи, начинаем обработку страниц..."),
        "pages_processed": (65, "Признаки страниц собраны, считаем score..."),
        "pages_processed_partial": (65, "Часть страниц обработана, считаем score..."),
        "scored": (82, "Скоры рассчитаны, запускаем AI-анализ..."),
        "scored_partial": (82, "Скоры частично рассчитаны, запускаем AI-анализ..."),
        "done_ai": (100, "Готово: анализ завершен с AI."),
        "done_ai_partial": (100, "Готово: анализ завершен с AI (частично)."),
        "done_no_ai": (100, "Готово: анализ завершен без AI."),
        "done_no_ai_partial": (100, "Готово: анализ завершен без AI (частично)."),
        "failed": (100, "Анализ завершился с ошибкой."),
        "captcha_detected": (100, "Обнаружена капча, требуется ручное действие."),
        "no_results": (100, "По запросу нет SERP-результатов."),
        "scoring_skipped": (100, "Скоринг пропущен: недостаточно данных."),
    }
    return mapping.get(status, (10, "Анализ выполняется..."))

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)
