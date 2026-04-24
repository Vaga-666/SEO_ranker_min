import json
import re
from typing import Any

from openai import OpenAI

from app.config import Settings, get_settings
from app.models import PageFeature
from app.services.debug_io import append_run_log


def serp_intent_analyzer(
    query: str,
    keywords: list[str],
    top_results: list[dict[str, Any]],
    page_features_short: list[dict[str, Any]],
    run_id: int | None = None,
) -> dict[str, Any] | None:
    settings = get_settings()
    if not settings.openai_api_key:
        if run_id is not None:
            append_run_log(run_id, "ai.disabled", reason="empty_api_key", stage="serp_intent")
        return None

    prompt = {
        "task": "Analyze SERP intent and output strict JSON.",
        "query": query,
        "keywords": keywords,
        "keyword_handling_rule": (
            "Do not analyze keywords as one long phrase. "
            "Each line / each list item is a separate search query. "
            "Build one shared SEO strategy for the target page across the whole keyword group."
        ),
        "top_results": top_results,
        "page_features_short": page_features_short,
        "output_schema": {
            "dominant_intent": "string",
            "dominant_page_type": "string",
            "required_blocks": ["string"],
            "common_entities": ["string"],
            "commercial_expectations": ["string"],
            "trust_expectations": ["string"],
        },
    }
    return _ask_json(prompt, settings=settings, run_id=run_id, stage="serp_intent")


def page_analyzer(
    query: str,
    keywords: list[str],
    serp_summary: dict[str, Any],
    feature: PageFeature,
    run_id: int | None = None,
) -> dict[str, Any] | None:
    settings = get_settings()
    if not settings.openai_api_key:
        if run_id is not None:
            append_run_log(
                run_id,
                "ai.disabled",
                reason="empty_api_key",
                stage="page_analyzer",
                source_url=feature.source_url,
            )
        return None

    payload = {
        "task": "Analyze page and output strict JSON scores and recommendations.",
        "query": query,
        "keywords": keywords,
        "keyword_handling_rule": (
            "Do not analyze keywords as one long phrase. "
            "Each line / each list item is a separate search query. "
            "Build one shared SEO strategy for the target page across the whole keyword group."
        ),
        "serp_summary": serp_summary,
        "page_features": {
            "source_url": feature.source_url,
            "source_type": feature.source_type,
            "title": feature.title,
            "h1": feature.h1,
            "meta_description": feature.meta_description,
            "h2_h3_json": feature.h2_h3_json,
            "has_faq": feature.has_faq,
            "has_tables": feature.has_tables,
            "has_lists": feature.has_lists,
            "has_cta": feature.has_cta,
            "has_forms": feature.has_forms,
            "has_prices": feature.has_prices,
            "has_reviews": feature.has_reviews,
            "has_contacts": feature.has_contacts,
            "internal_links_count": feature.internal_links_count,
            "external_links_count": feature.external_links_count,
            "has_schema_org": feature.has_schema_org,
        },
        "output_schema": {
            "page_type": "string",
            "intent_fit_score": "number_0_100",
            "structure_fit_score": "number_0_100",
            "semantic_score": "number_0_100",
            "commercial_score": "number_0_100",
            "trust_score": "number_0_100",
            "strengths": ["string"],
            "weaknesses": ["string"],
            "missing_blocks": ["string"],
            "missing_entities": ["string"],
            "recommendations_short": ["string"],
        },
    }
    return _ask_json(
        payload,
        settings=settings,
        run_id=run_id,
        stage="page_analyzer",
        source_url=feature.source_url,
    )


def _ask_json(
    payload: dict[str, Any],
    settings: Settings,
    run_id: int | None = None,
    stage: str = "unknown",
    source_url: str | None = None,
) -> dict[str, Any] | None:
    client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)

    # Attempt 1: strict JSON mode.
    try:
        response = client.chat.completions.create(
            model=settings.openai_model,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an SEO analyst. Always return valid JSON only, no markdown, no prose. "
                        "When keywords are provided as a list, treat each element as a separate query and build a shared SEO strategy."
                    ),
                },
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0.2,
        )
        content = response.choices[0].message.content
        parsed = _parse_json_content(content)
        if parsed is not None:
            if run_id is not None:
                append_run_log(run_id, "ai.success", stage=stage, source_url=source_url, method="json_mode")
            return parsed
    except Exception as exc:
        if run_id is not None:
            append_run_log(
                run_id,
                "ai.error",
                stage=stage,
                source_url=source_url,
                method="json_mode",
                error=f"{exc.__class__.__name__}: {str(exc) or repr(exc)}",
            )

    # Attempt 2: plain completion with JSON extraction fallback.
    try:
        response = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an SEO analyst. Return valid JSON only. "
                        "No markdown code fences, no comments. "
                        "When keywords are provided as a list, treat each element as a separate query and build a shared SEO strategy."
                    ),
                },
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0.2,
        )
        content = response.choices[0].message.content
        parsed = _parse_json_content(content)
        if parsed is not None:
            if run_id is not None:
                append_run_log(run_id, "ai.success", stage=stage, source_url=source_url, method="plain_json_extract")
            return parsed
    except Exception as exc:
        if run_id is not None:
            append_run_log(
                run_id,
                "ai.error",
                stage=stage,
                source_url=source_url,
                method="plain_json_extract",
                error=f"{exc.__class__.__name__}: {str(exc) or repr(exc)}",
            )

    if run_id is not None:
        append_run_log(run_id, "ai.failed", stage=stage, source_url=source_url)
    return None


def _parse_json_content(content: str | None) -> dict[str, Any] | None:
    if not content:
        return None
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    extracted = _extract_json_object(content)
    if not extracted:
        return None
    try:
        parsed = json.loads(extracted)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        return None
    return None


def _extract_json_object(text: str) -> str | None:
    # Minimal robust extraction for accidental prose wrappers around JSON.
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None
    return match.group(0)
