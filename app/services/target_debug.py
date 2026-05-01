import json
import re
from typing import Any

from bs4 import BeautifulSoup

from app.services.feature_extractor import (
    COMMERCIAL_MARKERS,
    CONTACT_MARKERS,
    COURSE_MARKERS,
    CTA_MARKERS,
    FAQ_MARKERS,
    FORM_MARKERS,
    REVIEW_MARKERS,
)

SCHEMA_MARKERS = ["application/ld+json", "Organization", "WebSite", "WebPage", "FAQPage", "BreadcrumbList", "ItemList", "Course"]


def build_target_html_debug(
    *,
    target_url: str,
    fetch_method: str,
    html: str,
    extracted_features: dict[str, Any] | None = None,
    calculated_scores: dict[str, Any] | None = None,
    score_reasons: dict[str, Any] | None = None,
) -> dict[str, Any]:
    soup = BeautifulSoup(html or "", "lxml")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    h1 = [h.get_text(" ", strip=True) for h in soup.find_all("h1") if h.get_text(" ", strip=True)]
    h2_h3 = [h.get_text(" ", strip=True) for h in soup.find_all(["h2", "h3"]) if h.get_text(" ", strip=True)]
    ld_json_types = sorted(_extract_ld_json_types(soup))

    body = soup.body
    body_text = body.get_text(" ", strip=True) if body else soup.get_text(" ", strip=True)
    lowered_html = (html or "").lower()
    extracted = extracted_features or {}
    score_debug = _merge_score_debug(calculated_scores or {}, score_reasons or {})

    raw_markers = {
        "cta_markers": _found_markers(html, body_text, CTA_MARKERS),
        "form_markers": _found_markers(html, body_text, FORM_MARKERS),
        "faq_markers": _found_markers(html, body_text, FAQ_MARKERS),
        "review_markers": _found_markers(html, body_text, REVIEW_MARKERS),
        "contact_markers": _found_markers(html, body_text, CONTACT_MARKERS),
        "commercial_markers": _found_markers(html, body_text, COMMERCIAL_MARKERS),
        "course_markers": _found_markers(html, body_text, COURSE_MARKERS),
        "schema_types": ld_json_types,
        "schema_markers": _found_markers(html, body_text, SCHEMA_MARKERS),
    }

    payload: dict[str, Any] = {
        "target_url": target_url,
        "fetch_method": fetch_method,
        "html_length": len(html or ""),
        "text_length": len(body_text),
        "title": title,
        "h1": h1,
        "h2_h3": h2_h3,
        "raw_markers": raw_markers,
        "extracted_features": {
            "has_cta": bool(extracted.get("has_cta")),
            "has_forms": bool(extracted.get("has_forms")),
            "has_lead_form": bool(extracted.get("has_lead_form")),
            "has_faq": bool(extracted.get("has_faq")),
            "has_reviews": bool(extracted.get("has_reviews")),
            "has_contacts": bool(extracted.get("has_contacts")),
            "has_schema": bool(extracted.get("has_schema")),
            "has_prices": bool(extracted.get("has_prices")),
            "has_courses": bool(extracted.get("has_courses")),
            "internal_links_count": int(extracted.get("internal_links_count") or 0),
            "external_links_count": int(extracted.get("external_links_count") or 0),
        },
        "score_debug": score_debug,
        # Legacy keys still used in the current run detail template/log summaries.
        "title_text": title,
        "h1_list": h1,
        "h2_h3_count": len(h2_h3),
        "has_react_root_only": _has_react_root_only(soup),
        "has_ld_json": bool(ld_json_types) or "application/ld+json" in lowered_html,
        "ld_json_types": ld_json_types,
        "has_cta_marker": bool(raw_markers["cta_markers"]),
        "has_form_tag": bool(soup.find("form")) or "<form" in lowered_html,
        "input_count": len(soup.find_all("input")),
        "textarea_count": len(soup.find_all("textarea")),
        "button_count": len(soup.find_all("button")),
        "has_mailto": "mailto:" in lowered_html,
        "has_faq_marker": bool(raw_markers["faq_markers"]),
        "has_reviews_marker": bool(raw_markers["review_markers"]),
        "has_contacts_marker": bool(raw_markers["contact_markers"]),
        "has_courses_marker": bool(raw_markers["course_markers"]),
        "first_500_text_chars": body_text[:500],
        "found_markers": {
            "cta": raw_markers["cta_markers"],
            "form": raw_markers["form_markers"],
            "faq": raw_markers["faq_markers"],
            "reviews": raw_markers["review_markers"],
            "contacts": raw_markers["contact_markers"],
            "courses": raw_markers["course_markers"],
            "schema": raw_markers["schema_markers"],
        },
        "feature_flags": extracted,
        "calculated_scores": calculated_scores or {},
        "score_reasons": score_reasons or {},
    }
    return payload


def log_payload_fields(payload: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "target_url",
        "fetch_method",
        "html_length",
        "text_length",
        "title",
        "h1",
        "h2_h3_count",
        "has_react_root_only",
        "has_ld_json",
        "ld_json_types",
        "has_cta_marker",
        "has_form_tag",
        "input_count",
        "textarea_count",
        "button_count",
        "has_mailto",
        "has_faq_marker",
        "has_reviews_marker",
        "has_contacts_marker",
        "has_courses_marker",
        "first_500_text_chars",
    ]
    return {key: payload.get(key) for key in keys}


def feature_debug_dict(feature: Any) -> dict[str, Any]:
    if feature is None:
        return {}
    page_text = " ".join(
        [
            str(getattr(feature, "title", None) or ""),
            str(getattr(feature, "meta_description", None) or ""),
            str(getattr(feature, "h1", None) or ""),
            str(getattr(feature, "h2_h3_json", None) or ""),
            str(getattr(feature, "main_text", None) or ""),
        ]
    ).lower()
    return {
        "title": getattr(feature, "title", None),
        "meta_description": getattr(feature, "meta_description", None),
        "h1": getattr(feature, "h1", None),
        "h2_h3_json": getattr(feature, "h2_h3_json", None),
        "text_length": len(getattr(feature, "main_text", None) or ""),
        "has_faq": bool(getattr(feature, "has_faq", False)),
        "has_tables": bool(getattr(feature, "has_tables", False)),
        "has_lists": bool(getattr(feature, "has_lists", False)),
        "has_cta": bool(getattr(feature, "has_cta", False)),
        "has_forms": bool(getattr(feature, "has_forms", False)),
        "has_lead_form": bool(getattr(feature, "has_lead_form", False) or getattr(feature, "has_forms", False)),
        "has_prices": bool(getattr(feature, "has_prices", False)),
        "has_courses": bool(getattr(feature, "has_courses", False))
        or any(marker.lower() in page_text for marker in COURSE_MARKERS),
        "has_reviews": bool(getattr(feature, "has_reviews", False)),
        "has_contacts": bool(getattr(feature, "has_contacts", False)),
        "internal_links_count": int(getattr(feature, "internal_links_count", 0) or 0),
        "external_links_count": int(getattr(feature, "external_links_count", 0) or 0),
        "has_schema": bool(getattr(feature, "has_schema_org", False)),
    }


def score_debug_dict(score: Any) -> dict[str, Any]:
    if score is None:
        return {}
    return {
        "intent_fit": getattr(score, "intent_fit", None),
        "structure_fit": getattr(score, "structure_fit", None),
        "semantic_coverage": getattr(score, "semantic_coverage", None),
        "commercial_fit": getattr(score, "commercial_fit", None),
        "trust_fit": getattr(score, "trust_fit", None),
        "total_score": getattr(score, "total_score", None),
    }


def _merge_score_debug(scores: dict[str, Any], reasons: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for key in ["intent_fit", "structure_fit", "semantic_coverage", "commercial_fit", "trust_fit"]:
        reason = reasons.get(key) if isinstance(reasons, dict) else None
        if isinstance(reason, dict):
            merged[key] = {
                "score": reason.get("score", scores.get(key)),
                "passed_rules": reason.get("passed_rules", []),
                "failed_rules": reason.get("failed_rules", []),
            }
            for optional_key in ["passed", "total", "details", "text_length"]:
                if optional_key in reason:
                    merged[key][optional_key] = reason[optional_key]
        else:
            merged[key] = {"score": scores.get(key), "passed_rules": [], "failed_rules": []}
    return merged


def _found_markers(html: str, text: str, markers: list[str]) -> list[str]:
    haystack = f"{html or ''}\n{text or ''}".lower()
    return [marker for marker in markers if marker.lower() in haystack]


def _has_react_root_only(soup: BeautifulSoup) -> bool:
    body = soup.body
    if body is None:
        return False
    root = body.find(id="root")
    if root is None:
        return False
    text = body.get_text(" ", strip=True)
    return len(text) < 250 and not body.find(["h1", "h2", "h3"])


def _extract_ld_json_types(soup: BeautifulSoup) -> set[str]:
    types: set[str] = set()
    for script in soup.find_all("script", attrs={"type": re.compile(r"application/ld\+json", re.IGNORECASE)}):
        raw = script.string or script.get_text() or ""
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except Exception:
            for known in SCHEMA_MARKERS[1:]:
                if known.lower() in raw.lower():
                    types.add(known)
            continue
        _collect_schema_types(data, types)
    return types


def _collect_schema_types(value: object, out: set[str]) -> None:
    if isinstance(value, dict):
        type_value = value.get("@type")
        if isinstance(type_value, str):
            out.add(type_value)
        elif isinstance(type_value, list):
            out.update([item for item in type_value if isinstance(item, str)])
        for nested in value.values():
            if isinstance(nested, (dict, list)):
                _collect_schema_types(nested, out)
    elif isinstance(value, list):
        for item in value:
            _collect_schema_types(item, out)
