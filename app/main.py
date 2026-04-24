import json
import re
from pathlib import Path
import sys
import threading
from datetime import datetime

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import Base, SessionLocal, engine, ensure_sqlite_schema, get_db
from app.models import (
    AnalysisKeyword,
    AnalysisRun,
    PageFeature,
    PageScore,
    PageSnapshot,
    Recommendation,
    SerpResult,
)
from app.services.keyword_parser import parse_keywords
from app.services.pipeline import (
    run_analysis_step_ai_and_recommendations,
    run_analysis_step_serp,
    run_analysis_step_serp_interactive,
    run_analysis_with_manual_serp,
)

app = FastAPI(title="SEO Ranker MVP")
templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.on_event("startup")
def startup() -> None:
    Base.metadata.create_all(bind=engine)
    ensure_sqlite_schema(engine)


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "page_title": "SEO Ranker MVP",
            "form_error": None,
            "form_values": {"keywords": "", "target_url": "", "manual_top_urls": ""},
        },
    )


@app.post("/analyze")
def start_analysis(
    request: Request,
    keywords: str = Form(""),
    query: str = Form(""),
    target_url: str = Form(...),
    manual_top_urls: str = Form(""),
    db: Session = Depends(get_db),
):
    raw_keywords = _resolve_raw_keywords(keywords=keywords, query=query)
    keywords_list = parse_keywords(raw_keywords)
    if not keywords_list:
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "page_title": "SEO Ranker MVP",
                "form_error": "Введите хотя бы одно ключевое слово",
                "form_values": {
                    "keywords": raw_keywords,
                    "target_url": target_url,
                    "manual_top_urls": manual_top_urls,
                },
            },
            status_code=400,
        )

    run = _create_analysis_run(
        db=db,
        raw_keywords=raw_keywords,
        keywords_list=keywords_list,
        target_url=target_url,
    )

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
    keywords: str = Form(""),
    query: str = Form(""),
    target_url: str = Form(...),
    manual_top_urls: str = Form(""),
    db: Session = Depends(get_db),
):
    raw_keywords = _resolve_raw_keywords(keywords=keywords, query=query)
    keywords_list = parse_keywords(raw_keywords)
    if not keywords_list:
        return JSONResponse(
            {"detail": "Введите хотя бы одно ключевое слово"},
            status_code=400,
        )

    run = _create_analysis_run(
        db=db,
        raw_keywords=raw_keywords,
        keywords_list=keywords_list,
        target_url=target_url,
    )

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


@app.post("/runs/{run_id}/rerun-ai-codex")
def rerun_ai_codex(run_id: int, request: Request, db: Session = Depends(get_db)):
    run = db.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    running_statuses = {
        "created",
        "running",
        "running_keywords",
        "running_manual_serp",
        "running_interactive_serp",
        "serp_collected",
        "serp_collected_partial",
        "serp_collected_manual",
        "serp_collected_manual_partial",
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

    keywords = (
        db.query(AnalysisKeyword)
        .filter(AnalysisKeyword.run_id == run.id)
        .order_by(AnalysisKeyword.id.asc())
        .all()
    )
    serp_results = (
        db.query(SerpResult)
        .filter(SerpResult.analysis_run_id == run.id)
        .order_by(SerpResult.keyword_id.asc().nulls_first(), SerpResult.position.asc(), SerpResult.id.asc())
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
    keyword_groups_map: dict[int | None, dict] = {
        keyword.id: {
            "keyword_id": keyword.id,
            "keyword": keyword.keyword,
            "status": keyword.status,
            "error_message": keyword.error_message,
            "rows": [],
        }
        for keyword in keywords
    }
    for item in serp_results:
        score_row = score_map.get(item.url.rstrip("/"))
        row_payload = {
            "real_position": item.position,
            "our_position": score_row.internal_rank if score_row else None,
            "keyword": item.keyword.keyword if item.keyword else None,
            "url": item.url,
            "title": item.title,
            "total_score": round(score_row.total_score, 2) if score_row else None,
        }
        top_rows.append(row_payload)
        group = keyword_groups_map.get(item.keyword_id)
        if group is None:
            keyword_groups_map[item.keyword_id] = {
                "keyword_id": item.keyword_id,
                "keyword": item.keyword.keyword if item.keyword else "Без ключевого слова",
                "status": "done",
                "error_message": None,
                "rows": [row_payload],
            }
        else:
            group["rows"].append(row_payload)

    keyword_groups = list(keyword_groups_map.values())
    if not keyword_groups and run.query.strip():
        keyword_groups = [
            {
                "keyword_id": None,
                "keyword": run.query,
                "status": "pending",
                "error_message": None,
                "rows": [],
            }
        ]

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

    recommendations_by_priority = {
        "urgent": [r for r in recommendations if r.priority == "urgent"],
        "medium": [r for r in recommendations if r.priority == "medium"],
        "later": [r for r in recommendations if r.priority == "later"],
    }
    running_statuses = {
        "created",
        "running",
        "running_keywords",
        "running_manual_serp",
        "running_interactive_serp",
        "running_ai_codex_manual",
        "serp_collected",
        "serp_collected_partial",
        "serp_collected_manual",
        "serp_collected_manual_partial",
        "serp_collected_interactive",
        "pages_processed",
        "pages_processed_partial",
        "scored",
        "scored_partial",
    }
    is_running = run.status in running_statuses
    running_seconds = int((datetime.utcnow() - run.created_at).total_seconds()) if is_running else 0
    status_hints = {
        "running_keywords": "Идет поочередный сбор SERP по каждому ключевому слову.",
        "running_interactive_serp": "Открыто окно браузера, пройдите капчу и дождитесь завершения.",
        "running_ai_codex_manual": "Выполняем повторный AI-анализ и формируем рекомендации для Codex.",
        "running": "Идет сбор SERP.",
        "running_manual_serp": "Применяется ручной список top-10.",
        "serp_collected_partial": "Часть ключевых слов собрана, продолжаем обработку доступных результатов.",
        "serp_collected_manual_partial": "Часть ручных SERP-данных обработана, продолжаем анализ.",
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
            "keywords": keywords,
            "keyword_groups": keyword_groups,
            "serp_results": serp_results,
            "snapshots": snapshots,
            "features_count": features_count,
            "top_rows": top_rows,
            "target_vs_top": target_vs_top,
            "gap_report": gap_report,
            "serp_summary_data": serp_summary_data,
            "codex_advice": codex_advice,
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
    message = _build_keyword_progress_message(db=db, run=run, message=message)
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
        "running": (15, "Собираем выдачу по ключевым словам..."),
        "running_keywords": (15, "Анализируем ключевые слова..."),
        "running_keyword_serp": (18, "Собираем выдачу по ключевым словам..."),
        "running_manual_serp": (15, "Применяем ручной top-10 по ключевым словам..."),
        "running_interactive_serp": (15, "Ожидаем прохождение капчи..."),
        "running_ai_codex_manual": (88, "Пересчитываем AI-анализ и рекомендации для Codex..."),
        "serp_collected": (35, "Обрабатываем страницы конкурентов..."),
        "serp_collected_partial": (35, "Обрабатываем страницы конкурентов по доступным ключам..."),
        "serp_collected_manual": (35, "Обрабатываем страницы конкурентов..."),
        "serp_collected_manual_partial": (35, "Обрабатываем страницы конкурентов по доступным ключам..."),
        "serp_collected_interactive": (35, "Обрабатываем страницы конкурентов..."),
        "keywords_processed": (65, "Считаем SEO score..."),
        "pages_processed": (65, "Считаем SEO score..."),
        "pages_processed_partial": (65, "Считаем SEO score по доступным данным..."),
        "scored": (82, "Готовим AI-анализ и рекомендации..."),
        "scored_partial": (82, "Готовим AI-анализ и рекомендации по доступным данным..."),
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


def _resolve_raw_keywords(keywords: str, query: str) -> str:
    return (keywords or "").strip() or (query or "").strip()


def _create_analysis_run(
    db: Session,
    raw_keywords: str,
    keywords_list: list[str],
    target_url: str,
) -> AnalysisRun:
    run = AnalysisRun(query=raw_keywords.strip() or keywords_list[0], target_url=target_url.strip(), status="created")
    db.add(run)
    db.flush()

    for keyword in keywords_list:
        db.add(AnalysisKeyword(run_id=run.id, keyword=keyword, status="pending"))

    db.commit()
    db.refresh(run)
    return run


def _build_keyword_progress_message(db: Session, run: AnalysisRun, message: str) -> str:
    if run.status not in {
        "running",
        "running_keywords",
        "running_keyword_serp",
        "running_manual_serp",
        "running_interactive_serp",
        "serp_collected_partial",
        "serp_collected_manual_partial",
    }:
        return message

    keyword_rows = (
        db.query(AnalysisKeyword)
        .filter(AnalysisKeyword.run_id == run.id)
        .order_by(AnalysisKeyword.id.asc())
        .all()
    )
    total = len(keyword_rows)
    if total <= 0:
        return message

    completed = sum(1 for row in keyword_rows if row.status in {"done", "failed"})
    current_index = completed + 1 if completed < total else total
    running = sum(1 for row in keyword_rows if row.status == "running")

    suffix = f" Ключ {current_index} из {total}."
    if running > 0 and completed < total:
        suffix = f" Ключ {current_index} из {total} (в работе: {running})."
    return message + suffix

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
