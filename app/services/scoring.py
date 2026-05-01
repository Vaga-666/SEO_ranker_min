import json
import re
from dataclasses import dataclass
from typing import Any

from app.models import PageFeature
from app.scoring_config import SCORING_WEIGHTS


@dataclass
class ScoreResult:
    intent_fit: float
    structure_fit: float
    semantic_coverage: float
    commercial_fit: float
    trust_fit: float
    total_score: float


@dataclass
class ScoreDebugResult:
    score: ScoreResult
    reasons: dict[str, Any]


def score_page_feature(query: str, feature: PageFeature) -> ScoreResult:
    return score_page_feature_debug(query=query, feature=feature).score


def score_page_feature_debug(query: str, feature: PageFeature) -> ScoreDebugResult:
    page_text = _page_text(feature)
    h2_h3 = _load_h2_h3(feature)
    query_tokens = _normalize_tokens(query)
    text_tokens = set(_normalize_tokens(page_text))

    matched_tokens = [token for token in query_tokens if token in text_tokens]
    intent_rules = [
        ("query_token_overlap", bool(matched_tokens), {"matched_tokens": matched_tokens, "query_tokens": query_tokens}),
        ("mentions_online_school", _contains_any(page_text, ["онлайн-школа", "онлайн школа"])),
        ("mentions_online_learning", _contains_any(page_text, ["онлайн обучение", "дистанционное обучение", "курсы онлайн"])),
        ("mentions_education_platform", _contains_any(page_text, ["образовательная платформа", "платформа обучения"])),
        ("mentions_students_or_teachers", _contains_any(page_text, ["ученики", "ученик", "студенты", "студент", "преподаватели", "преподаватель"])),
        ("mentions_learning_process", _contains_any(page_text, ["учебный процесс", "практические задания", "домашние задания"])),
    ]
    intent_fit, intent_debug = _score_rules(intent_rules)

    structure_rules = [
        ("has_title", bool(getattr(feature, "title", None))),
        ("has_h1", bool(getattr(feature, "h1", None))),
        ("has_h2_h3", bool(h2_h3)),
        ("has_lists", bool(getattr(feature, "has_lists", False))),
        ("has_faq", bool(getattr(feature, "has_faq", False))),
        ("has_cta", bool(getattr(feature, "has_cta", False))),
        ("has_courses_or_formats_block", _has_courses(feature) or _contains_any(page_text, ["форматы обучения", "доступные курсы"])),
        ("text_length_gt_1200", len(getattr(feature, "main_text", None) or "") > 1200),
    ]
    structure_fit, structure_debug = _score_rules(structure_rules)

    semantic_rules = [
        ("query_token_overlap", bool(matched_tokens), {"matched_tokens": matched_tokens, "query_tokens": query_tokens}),
        ("text_length_gt_1500", len(getattr(feature, "main_text", None) or "") > 1500),
        ("mentions_courses", _has_courses(feature) or _contains_any(page_text, ["курс", "курсы", "python", "sql"])),
        ("mentions_practice", _contains_any(page_text, ["практика", "практические задания", "задания"])),
        ("mentions_ai", _contains_any(page_text, ["ai", "ии", "нейросет", "chatgpt"])),
        ("mentions_support_or_feedback", _contains_any(page_text, ["поддержка", "проверка", "обратная связь", "фидбек"])),
    ]
    semantic_coverage, semantic_debug = _score_rules(semantic_rules)
    semantic_debug["text_length"] = len(getattr(feature, "main_text", None) or "")

    commercial_rules = [
        ("has_cta", bool(getattr(feature, "has_cta", False))),
        ("has_lead_form", _has_lead_form(feature)),
        ("has_contacts", bool(getattr(feature, "has_contacts", False))),
        ("has_courses", _has_courses(feature)),
        ("has_free_paid_or_pricing_markers", bool(getattr(feature, "has_prices", False))),
        ("has_buy_or_enrollment_markers", _contains_any(page_text, ["купить", "записаться", "начать бесплатно", "отправить заявку"])),
    ]
    commercial_fit, commercial_debug = _score_rules(commercial_rules)

    trust_rules = [
        ("has_contacts", bool(getattr(feature, "has_contacts", False))),
        ("has_reviews", bool(getattr(feature, "has_reviews", False))),
        ("has_schema_org", bool(getattr(feature, "has_schema_org", False))),
        ("has_email_or_mailto", _contains_any(page_text, ["email", "support@", "mailto:"])),
        ("has_teacher_or_expertise_block", _contains_any(page_text, ["преподаватель", "эксперт", "проверка", "ai экзаменатор"])),
        ("has_documents_or_license_links", _contains_any(page_text, ["лиценз", "документ", "оферта", "политика"])),
    ]
    trust_fit, trust_debug = _score_rules(trust_rules)

    total_score = (
        intent_fit * SCORING_WEIGHTS["intent_fit"]
        + structure_fit * SCORING_WEIGHTS["structure_fit"]
        + semantic_coverage * SCORING_WEIGHTS["semantic_coverage"]
        + commercial_fit * SCORING_WEIGHTS["commercial_fit"]
        + trust_fit * SCORING_WEIGHTS["trust_fit"]
    )

    score = ScoreResult(
        intent_fit=intent_fit,
        structure_fit=structure_fit,
        semantic_coverage=semantic_coverage,
        commercial_fit=commercial_fit,
        trust_fit=trust_fit,
        total_score=round(total_score, 2),
    )
    return ScoreDebugResult(
        score=score,
        reasons={
            "intent_fit": intent_debug,
            "structure_fit": structure_debug,
            "semantic_coverage": semantic_debug,
            "commercial_fit": commercial_debug,
            "trust_fit": trust_debug,
        },
    )


def _page_text(feature: Any) -> str:
    values = [
        getattr(feature, "title", None) or "",
        getattr(feature, "h1", None) or "",
        getattr(feature, "meta_description", None) or "",
        " ".join(_load_h2_h3(feature)),
        getattr(feature, "main_text", None) or "",
    ]
    return " ".join(values).lower()


def _load_h2_h3(feature: Any) -> list[str]:
    raw = getattr(feature, "h2_h3_json", None)
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except Exception:
        return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _has_lead_form(feature: Any) -> bool:
    return bool(getattr(feature, "has_lead_form", False) or getattr(feature, "has_forms", False))


def _has_courses(feature: Any) -> bool:
    return bool(getattr(feature, "has_courses", False)) or _contains_any(
        _page_text(feature),
        ["курсы", "доступные курсы", "курс", "python", "sql", "обучение", "форматы обучения"],
    )


def _score_rules(rules: list[tuple[str, bool] | tuple[str, bool, dict[str, Any]]]) -> tuple[float, dict[str, Any]]:
    passed_rules: list[str] = []
    failed_rules: list[str] = []
    details: dict[str, Any] = {}
    for rule in rules:
        name = rule[0]
        passed = bool(rule[1])
        if passed:
            passed_rules.append(name)
        else:
            failed_rules.append(name)
        if len(rule) > 2:
            details[name] = rule[2]

    score = _to_100(len(passed_rules) / max(1, len(rules)))
    return score, {
        "score": score,
        "passed_rules": passed_rules,
        "failed_rules": failed_rules,
        "passed": len(passed_rules),
        "total": len(rules),
        "details": details,
    }


def _contains_any(haystack: str, needles: list[str]) -> bool:
    return any(needle.lower() in haystack for needle in needles)


def _normalize_tokens(text: str) -> list[str]:
    tokens = re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]+", text.lower())
    return [token for token in tokens if len(token) > 2]


def _to_100(value_0_1: float) -> float:
    return round(max(0.0, min(1.0, value_0_1)) * 100.0, 2)
