from app.models import PageFeature, PageScore, Recommendation


def build_basic_gap_recommendations(
    run_id: int,
    target_feature: PageFeature | None,
    target_score: PageScore | None,
    top_features: list[PageFeature],
    top_scores: list[PageScore],
) -> list[Recommendation]:
    recs: list[Recommendation] = []
    if not target_feature or not target_score:
        recs.append(
            Recommendation(
                analysis_run_id=run_id,
                source_url=None,
                priority="urgent",
                text="Не удалось полноценно оценить целевую страницу. Проверьте доступность URL.",
            )
        )
        return recs

    avg_total = _avg([s.total_score for s in top_scores])
    if avg_total is not None and target_score.total_score < avg_total - 8:
        recs.append(
            Recommendation(
                analysis_run_id=run_id,
                source_url=target_feature.source_url,
                priority="urgent",
                text="Суммарный score целевой страницы заметно ниже среднего top-10. Нужна доработка структуры и коммерческих блоков.",
            )
        )

    top_has_forms = any(f.has_forms for f in top_features)
    top_has_prices = any(f.has_prices for f in top_features)
    top_has_faq = any(f.has_faq for f in top_features)
    top_has_reviews = any(f.has_reviews for f in top_features)
    top_has_schema = any(f.has_schema_org for f in top_features)

    if top_has_forms and not target_feature.has_forms:
        recs.append(
            Recommendation(
                analysis_run_id=run_id,
                source_url=target_feature.source_url,
                priority="urgent",
                text="Добавьте форму заявки или контакта: у конкурентов это типовой блок.",
            )
        )
    if top_has_prices and not target_feature.has_prices:
        recs.append(
            Recommendation(
                analysis_run_id=run_id,
                source_url=target_feature.source_url,
                priority="medium",
                text="Добавьте ценовые ориентиры или тарифные блоки для усиления коммерческого интента.",
            )
        )
    if top_has_faq and not target_feature.has_faq:
        recs.append(
            Recommendation(
                analysis_run_id=run_id,
                source_url=target_feature.source_url,
                priority="medium",
                text="Добавьте FAQ-блок по ключевым вопросам пользователей.",
            )
        )
    if top_has_reviews and not target_feature.has_reviews:
        recs.append(
            Recommendation(
                analysis_run_id=run_id,
                source_url=target_feature.source_url,
                priority="medium",
                text="Добавьте отзывы/кейсы для усиления доверия.",
            )
        )
    if top_has_schema and not target_feature.has_schema_org:
        recs.append(
            Recommendation(
                analysis_run_id=run_id,
                source_url=target_feature.source_url,
                priority="later",
                text="Добавьте schema.org-разметку для структурирования контента.",
            )
        )

    if target_feature.internal_links_count < 3:
        recs.append(
            Recommendation(
                analysis_run_id=run_id,
                source_url=target_feature.source_url,
                priority="later",
                text="Усильте внутреннюю перелинковку со смежными страницами.",
            )
        )

    return recs


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)
