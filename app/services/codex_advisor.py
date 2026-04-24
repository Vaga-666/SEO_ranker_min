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
            "to improve target page content and ranking potential across the full keyword group."
        ),
        "language": "ru",
        "context": context,
        "constraints": [
            "No generic advice without evidence from context.",
            "Focus on content structure, semantic coverage, commercial and trust signals.",
            "Do not analyze keywords as one long phrase. Each line / each list item is a separate search query. Build one shared SEO strategy for the whole keyword group.",
            "Recommendations must be implementable on target page.",
            "Return valid JSON only.",
        ],
        "output_schema": {
            "keywords": ["string"],
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
                        "Return strict JSON only and base conclusions on provided data. "
                        "Do not analyze keywords as one long phrase. Each list item is a separate search query. "
                        "Build a shared SEO strategy for the whole keyword group."
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
    merged.setdefault("keywords", fallback["keywords"])
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
    query = str(context.get("query") or "")
    keywords = [str(item).strip() for item in (context.get("keywords") or []) if str(item).strip()]
    target_url = str(context.get("target_url") or "")

    if not keywords and query.strip():
        keywords = [query.strip()]

    primary_keyword = keywords[0] if keywords else query
    keywords_block = "\n".join(f"- {keyword}" for keyword in keywords) if keywords else f"- {query}"

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
                "area": "Structure",
                "problem": "The target page structure is weaker than top-10 across the keyword group.",
                "evidence": (
                    f"structure_fit diff={structure_diff}; "
                    + (
                        f"missing_blocks={', '.join(missing_blocks)}"
                        if missing_blocks
                        else "required structural blocks are weaker than competitors"
                    )
                ),
                "action": "Add missing blocks and rebuild the page flow: intent -> benefits -> proof -> CTA.",
                "expected_impact": "Higher structure relevance and better fit for multiple keyword intents.",
            }
        )

    if semantic_diff < -10 or missing_entities:
        fixes.append(
            {
                "priority": "high",
                "area": "Semantic coverage",
                "problem": "The page does not cover enough entities and subtopics across the keyword cluster.",
                "evidence": (
                    f"semantic_coverage diff={semantic_diff}; "
                    + (
                        f"missing_entities={', '.join(missing_entities[:8])}"
                        if missing_entities
                        else "semantic coverage is below the group average"
                    )
                ),
                "action": "Expand H2/H3 sections and body copy to cover repeated entities, intents, and use cases across the keyword set.",
                "expected_impact": "Better semantic coverage and stronger relevance for the full keyword group.",
            }
        )

    if commercial_diff < -8 or commercial_gaps:
        fixes.append(
            {
                "priority": "medium",
                "area": "Commercial intent",
                "problem": "Commercial signals are weaker than competitors for several target queries.",
                "evidence": (
                    f"commercial_fit diff={commercial_diff}; "
                    + (
                        f"gaps={', '.join(commercial_gaps)}"
                        if commercial_gaps
                        else "CTA / offer / pricing signals are weaker than top results"
                    )
                ),
                "action": "Add pricing anchors, clear CTA sections, and concise conversion-oriented blocks that support all target queries.",
                "expected_impact": "Better conversion intent alignment and stronger commercial fit.",
            }
        )

    if trust_diff < -8 or trust_gaps:
        fixes.append(
            {
                "priority": "medium",
                "area": "Trust",
                "problem": "Trust signals are weaker than top-10 across the keyword group.",
                "evidence": (
                    f"trust_fit diff={trust_diff}; "
                    + (
                        f"gaps={', '.join(trust_gaps)}"
                        if trust_gaps
                        else "trust proof is weaker than competing pages"
                    )
                ),
                "action": "Add testimonials, cases, credentials, contact proof, and structured trust sections that support the whole keyword group.",
                "expected_impact": "Higher perceived credibility and better trust fit.",
            }
        )

    if not fixes:
        fixes.append(
            {
                "priority": "low",
                "area": "Iteration",
                "problem": "No single critical gap dominates, but the page still trails the top results.",
                "evidence": f"total_score diff={total_diff}",
                "action": "Iteratively improve structure, semantic depth, and internal linking for the keyword set.",
                "expected_impact": "Steady gains in ranking readiness and content quality.",
            }
        )

    content_outline = [
        f"H1: Core positioning for the shared intent group led by '{primary_keyword}'.",
        "H2: Who the solution is for and which problems it solves across the keyword set.",
        "H2: Main program / product / service details covering all key search intents.",
        "H2: Pricing or offer comparison block with clear next steps.",
        "H2: Cases, reviews, or proof that supports commercial and trust intent.",
        "H2: FAQ covering repeated objections and repeated questions across the keyword group.",
    ]
    if missing_blocks:
        content_outline.append("Add repeated missing blocks from top-10: " + ", ".join(missing_blocks[:5]))

    copy_blocks = [
        "Hero value proposition that covers the full group of target SEO queries.",
        "Benefits block that maps one page offer to several search intents without splitting into separate pages.",
        "CTA block with one clear next step and supporting proof.",
        "Trust block with testimonials, cases, credentials, and credibility markers.",
    ]

    quick_wins = [
        "Rewrite title and H1 so they align with the core keyword cluster instead of a single phrase.",
        "Add 1-2 CTA sections tied to the main conversion intent of the keyword group.",
        "Add FAQ entries for repeated questions and objections seen across multiple keywords.",
        "Expand headings so missing entities from the keyword group are covered explicitly.",
    ]

    codex_task_prompt = (
        "Ты SEO-редактор и контент-стратег для посадочной страницы.\n"
        f"Целевая страница: {target_url}\n"
        f"Исходный ввод: {query}\n\n"
        "Не анализируй keywords как одну длинную фразу. Каждая строка/каждый элемент списка — отдельный поисковый запрос. "
        "Нужно сформировать общую SEO-стратегию для target page с учетом всей группы ключей.\n\n"
        "Целевые SEO-запросы:\n"
        f"{keywords_block}\n\n"
        "Подготовь обновленную структуру и контент страницы так, чтобы она лучше закрывала общие intent-ожидания, "
        "повторяющиеся semantic gaps, приоритетные блоки страницы и semantic coverage по всей группе запросов.\n"
        f"Ключевые разрывы: structure_diff={structure_diff}, semantic_diff={semantic_diff}, "
        f"commercial_diff={commercial_diff}, trust_diff={trust_diff}.\n"
        f"Недостающие блоки: {', '.join(missing_blocks) if missing_blocks else 'нет явных'}.\n"
        f"Недостающие сущности: {', '.join(missing_entities[:12]) if missing_entities else 'нет явных'}.\n"
        "Нужно выдать: 1) обновленную структуру H1-H3, 2) черновые тексты ключевых блоков, "
        "3) CTA и trust-блок, 4) список правок в формате 'было -> стало'."
    )

    diagnostics = [
        f"status={status}",
        f"keywords={json.dumps(keywords, ensure_ascii=False)}",
        f"total_score_diff={total_diff}",
        f"top_competitors_sample={json.dumps(top_urls, ensure_ascii=False)}",
        f"missing_blocks={json.dumps(missing_blocks, ensure_ascii=False)}",
        f"missing_entities_sample={json.dumps(missing_entities[:12], ensure_ascii=False)}",
    ]

    return {
        "keywords": keywords,
        "summary": "Сформирован SEO-бриф для Codex на основе сравнения target page с top-10 по всей группе ключевых слов.",
        "root_cause": (
            "Основное отставание связано с разрывом по структуре, semantic coverage, коммерческим и trust-сигналам "
            "относительно конкурентов по всей группе целевых ключевых слов."
        ),
        "priority_fixes": fixes[:8],
        "content_outline": content_outline[:10],
        "copy_blocks": copy_blocks[:8],
        "codex_task_prompt": codex_task_prompt,
        "quick_wins": quick_wins[:8],
        "diagnostics": diagnostics[:10],
    }
