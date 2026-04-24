import re
from dataclasses import dataclass

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


def score_page_feature(query: str, feature: PageFeature) -> ScoreResult:
    query_tokens = _normalize_tokens(query)
    page_text = " ".join(
        [
            feature.title or "",
            feature.h1 or "",
            feature.meta_description or "",
            feature.main_text or "",
        ]
    ).lower()
    text_tokens = set(_normalize_tokens(page_text))

    overlap = len([t for t in query_tokens if t in text_tokens])
    intent_fit = _to_100(overlap / max(1, len(query_tokens)))

    structure_signals = [
        1 if feature.h1 else 0,
        1 if feature.h2_h3_json and feature.h2_h3_json != "[]" else 0,
        1 if feature.has_lists else 0,
        1 if feature.has_tables else 0,
        1 if feature.main_text and len(feature.main_text) > 1200 else 0,
    ]
    structure_fit = _to_100(sum(structure_signals) / len(structure_signals))

    semantic_score_base = (overlap / max(1, len(query_tokens))) * 0.6
    length_bonus = min(1.0, (len(feature.main_text or "") / 3500.0)) * 0.4
    semantic_coverage = _to_100(semantic_score_base + length_bonus)

    commercial_signals = [
        1 if feature.has_cta else 0,
        1 if feature.has_forms else 0,
        1 if feature.has_prices else 0,
        1 if feature.has_contacts else 0,
    ]
    commercial_fit = _to_100(sum(commercial_signals) / len(commercial_signals))

    trust_signals = [
        1 if feature.has_reviews else 0,
        1 if feature.has_schema_org else 0,
        1 if feature.has_contacts else 0,
        1 if feature.external_links_count > 0 else 0,
    ]
    trust_fit = _to_100(sum(trust_signals) / len(trust_signals))

    total_score = (
        intent_fit * SCORING_WEIGHTS["intent_fit"]
        + structure_fit * SCORING_WEIGHTS["structure_fit"]
        + semantic_coverage * SCORING_WEIGHTS["semantic_coverage"]
        + commercial_fit * SCORING_WEIGHTS["commercial_fit"]
        + trust_fit * SCORING_WEIGHTS["trust_fit"]
    )

    return ScoreResult(
        intent_fit=intent_fit,
        structure_fit=structure_fit,
        semantic_coverage=semantic_coverage,
        commercial_fit=commercial_fit,
        trust_fit=trust_fit,
        total_score=round(total_score, 2),
    )


def _normalize_tokens(text: str) -> list[str]:
    tokens = re.findall(r"[a-zA-Zа-яА-Я0-9]+", text.lower())
    return [t for t in tokens if len(t) > 2]


def _to_100(value_0_1: float) -> float:
    return round(max(0.0, min(1.0, value_0_1)) * 100.0, 2)
