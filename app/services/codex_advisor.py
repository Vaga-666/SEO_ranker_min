import json
import re
from typing import Any

from openai import OpenAI

from app.config import get_settings


def codex_fix_advisor(context: dict[str, Any]) -> dict[str, Any]:
    fallback = _fallback_advice(context)
    settings = get_settings()
    if not settings.openai_api_key:
        return fallback

    payload = {
        "task": (
            "You are a senior SEO content strategist. "
            "Analyze target-vs-top10 gaps and produce a practical implementation brief for Codex "
            "to improve target page content and ranking potential."
        ),
        "language": "ru",
        "context": context,
        "constraints": [
            "No generic advice without evidence from context.",
            "Focus on content structure, semantic coverage, commercial and trust signals.",
            "Recommendations must be implementable on target page.",
            "Return valid JSON only.",
        ],
        "output_schema": {
            "summary": "string",
            "root_cause": "string",
            "priority_fixes": [
                {
                    "priority": "high|medium|low",
                    "area": "string",
                    "problem": "string",
                    "evidence": "string",
                    "action": "string",
                    "expected_impact": "string",
                }
            ],
            "content_outline": ["string"],
            "copy_blocks": ["string"],
            "codex_task_prompt": "string",
            "quick_wins": ["string"],
            "diagnostics": ["string"],
        },
    }

    try:
        client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)
        response = client.chat.completions.create(
            model=settings.openai_model,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an SEO content strategy lead. "
                        "Return strict JSON only and base conclusions on provided data."
                    ),
                },
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0.2,
        )
        content = response.choices[0].message.content
        parsed = _parse_json(content)
        if not parsed:
            return fallback
        return _merge_with_fallback(parsed, fallback)
    except Exception:
        return fallback


def _merge_with_fallback(parsed: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    merged = dict(parsed)
    merged.setdefault("summary", fallback["summary"])
    merged.setdefault("root_cause", fallback["root_cause"])
    merged.setdefault("priority_fixes", fallback["priority_fixes"])
    merged.setdefault("content_outline", fallback["content_outline"])
    merged.setdefault("copy_blocks", fallback["copy_blocks"])
    merged.setdefault("codex_task_prompt", fallback["codex_task_prompt"])
    merged.setdefault("quick_wins", fallback["quick_wins"])
    merged.setdefault("diagnostics", fallback["diagnostics"])
    return merged


def _parse_json(content: str | None) -> dict[str, Any] | None:
    if not content:
        return None
    try:
        value = json.loads(content)
        if isinstance(value, dict):
            return value
    except Exception:
        pass

    match = re.search(r"\{[\s\S]*\}", content)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
        if isinstance(value, dict):
            return value
    except Exception:
        return None
    return None


def _fallback_advice(context: dict[str, Any]) -> dict[str, Any]:
    status = str(context.get("status") or "").lower()
    metrics = context.get("target_vs_top", {}) or {}
    gaps = context.get("gap_report", {}) or {}
    top_competitors = context.get("top_competitors", []) or []
    query = context.get("query") or ""
    target_url = context.get("target_url") or ""

    def diff(metric_name: str) -> float:
        metric = metrics.get(metric_name) or {}
        target = float(metric.get("target", 0) or 0)
        top = float(metric.get("top_avg", 0) or 0)
        return round(target - top, 2)

    semantic_diff = diff("semantic_coverage")
    structure_diff = diff("structure_fit")
    commercial_diff = diff("commercial_fit")
    trust_diff = diff("trust_fit")
    total_diff = diff("total_score")

    missing_blocks = list(gaps.get("missing_blocks") or [])[:8]
    missing_entities = list(gaps.get("missing_entities") or [])[:12]
    commercial_gaps = list(gaps.get("commercial_gaps") or [])[:6]
    trust_gaps = list(gaps.get("trust_gaps") or [])[:6]

    top_urls = [str(item.get("url")) for item in top_competitors[:5] if item.get("url")]

    fixes: list[dict[str, str]] = []
    if structure_diff < -10 or missing_blocks:
        fixes.append(
            {
                "priority": "high",
                "area": "Структура страницы",
                "problem": "Структура target-страницы слабее среднего top-10.",
                "evidence": (
                    f"structure_fit diff={structure_diff}; "
                    + (f"missing_blocks={', '.join(missing_blocks)}" if missing_blocks else "обязательные блоки выражены слабо")
                ),
                "action": "Добавить недостающие блоки и выстроить последовательность: интент -> выгоды -> доказательства -> CTA.",
                "expected_impact": "Рост структуры и релевантности интенту, повышение общего score.",
            }
        )

    if semantic_diff < -10 or missing_entities:
        fixes.append(
            {
                "priority": "high",
                "area": "Семантическое покрытие",
                "problem": "Недостаточное покрытие подтем и сущностей из SERP.",
                "evidence": (
                    f"semantic_coverage diff={semantic_diff}; "
                    + (f"missing_entities={', '.join(missing_entities[:8])}" if missing_entities else "разрывы по сущностям заметны по score")
                ),
                "action": "Добавить разделы под недостающие сущности, усилить H2/H3 и текстовые кластеры по ключевым подтемам.",
                "expected_impact": "Лучшее соответствие поисковому спросу и расширение релевантности страницы.",
            }
        )

    if commercial_diff < -8 or commercial_gaps:
        fixes.append(
            {
                "priority": "medium",
                "area": "Коммерческие сигналы",
                "problem": "Страница уступает конкурентам по коммерческому интенту.",
                "evidence": (
                    f"commercial_fit diff={commercial_diff}; "
                    + (f"gaps={', '.join(commercial_gaps)}" if commercial_gaps else "у топов сильнее CTA/формы/тарифы")
                ),
                "action": "Добавить тарифы/ценовые ориентиры, явный CTA, короткую форму заявки и блок вариантов взаимодействия.",
                "expected_impact": "Рост коммерческого соответствия и конверсионного потенциала.",
            }
        )

    if trust_diff < -8 or trust_gaps:
        fixes.append(
            {
                "priority": "medium",
                "area": "Доверие (Trust)",
                "problem": "Недостаточно сигналов доверия относительно top-10.",
                "evidence": (
                    f"trust_fit diff={trust_diff}; "
                    + (f"gaps={', '.join(trust_gaps)}" if trust_gaps else "не хватает доказательств надежности")
                ),
                "action": "Добавить отзывы/кейсы, контактные данные, прозрачную информацию о продукте и schema.org разметку.",
                "expected_impact": "Повышение доверия пользователей и качества страницы в глазах поисковых систем.",
            }
        )

    if not fixes:
        fixes.append(
            {
                "priority": "low",
                "area": "Точечная оптимизация",
                "problem": "Критичных разрывов не выявлено, но есть потенциал роста по качеству контента.",
                "evidence": f"total_score diff={total_diff}",
                "action": "Провести итеративные улучшения контентных блоков и внутренней перелинковки.",
                "expected_impact": "Постепенное увеличение конкурентоспособности страницы.",
            }
        )

    content_outline = [
        "H1: Четкое позиционирование страницы под основной интент запроса.",
        "H2: Кому подходит решение и какие задачи закрывает.",
        "H2: Программа/функциональность/форматы обучения с конкретикой.",
        "H2: Тарифы или ценовые ориентиры + сравнение опций.",
        "H2: Кейсы и отзывы с измеримыми результатами.",
        "H2: FAQ по ключевым возражениям + финальный CTA.",
    ]
    if missing_blocks:
        content_outline.append("Добавить блоки из разрыва top-10: " + ", ".join(missing_blocks[:5]))

    copy_blocks = [
        "Короткий value proposition в первом экране.",
        "Список преимуществ в формате маркеров с фактами.",
        "Конкретный CTA-блок с понятным следующим шагом.",
        "Блок доверия: отзывы, кейсы, подтверждения.",
    ]

    quick_wins = [
        "Переписать title и H1 в явной связке с целевым запросом.",
        "Добавить 1-2 CTA в верхнюю и нижнюю часть страницы.",
        "Добавить FAQ по частым вопросам из интента.",
        "Усилить внутреннюю перелинковку на смежные разделы.",
    ]

    codex_task_prompt = (
        "Ты SEO-копирайтер и редактор посадочных страниц. "
        f"Запрос: '{query}'. Целевая страница: {target_url}. "
        "Нужно подготовить новую редакцию контента страницы, чтобы приблизить ее к уровню top-10. "
        f"Ключевые разрывы: structure_diff={structure_diff}, semantic_diff={semantic_diff}, "
        f"commercial_diff={commercial_diff}, trust_diff={trust_diff}. "
        f"Недостающие блоки: {', '.join(missing_blocks) if missing_blocks else 'нет явных'}. "
        f"Недостающие сущности: {', '.join(missing_entities[:12]) if missing_entities else 'нет явных'}. "
        "Сформируй: 1) обновленную структуру H1-H3, 2) черновые тексты ключевых блоков, "
        "3) CTA и trust-блок, 4) список конкретных правок в формате 'было -> стало'."
    )

    diagnostics = [
        f"status={status}",
        f"total_score_diff={total_diff}",
        f"top_competitors_sample={json.dumps(top_urls, ensure_ascii=False)}",
        f"missing_blocks={json.dumps(missing_blocks, ensure_ascii=False)}",
        f"missing_entities_sample={json.dumps(missing_entities[:12], ensure_ascii=False)}",
    ]

    return {
        "summary": "Сформирован SEO-бриф для Codex на основе сравнения target-страницы с top-10 и gap-анализа.",
        "root_cause": (
            "Основное отставание связано с разрывом по структуре, семантическому покрытию, "
            "коммерческим и trust-сигналам относительно конкурентов."
        ),
        "priority_fixes": fixes[:8],
        "content_outline": content_outline[:10],
        "copy_blocks": copy_blocks[:8],
        "codex_task_prompt": codex_task_prompt,
        "quick_wins": quick_wins[:8],
        "diagnostics": diagnostics[:10],
    }
