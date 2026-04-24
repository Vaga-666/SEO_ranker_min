from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.feature_extractor import extract_page_features
from app.services.scoring import score_page_feature


class _DummyFeature:
    def __init__(self, extracted):
        self.source_url = "https://example.com"
        self.source_type = "target"
        self.title = extracted.title
        self.meta_description = extracted.meta_description
        self.h1 = extracted.h1
        self.h2_h3_json = extracted.h2_h3_json
        self.main_text = extracted.main_text
        self.has_faq = extracted.has_faq
        self.has_tables = extracted.has_tables
        self.has_lists = extracted.has_lists
        self.has_cta = extracted.has_cta
        self.has_forms = extracted.has_forms
        self.has_prices = extracted.has_prices
        self.has_reviews = extracted.has_reviews
        self.has_contacts = extracted.has_contacts
        self.internal_links_count = extracted.internal_links_count
        self.external_links_count = extracted.external_links_count
        self.has_schema_org = extracted.has_schema_org


def main() -> None:
    html = """
    <html>
      <head>
        <title>Купить велосипед в Москве</title>
        <meta name="description" content="Велосипеды, цены, доставка, отзывы">
      </head>
      <body>
        <h1>Купить велосипед</h1>
        <h2>Каталог</h2>
        <h3>Доставка</h3>
        <p>Цена от 25000 ₽. Оставить заявку и получить консультацию.</p>
        <form><input type="text" /></form>
        <ul><li>Отзывы</li></ul>
        <a href="/contacts">Контакты</a>
        <a href="https://maps.example.org">Карта</a>
      </body>
    </html>
    """
    extracted = extract_page_features(html=html, page_url="https://example.com/bikes")
    feature = _DummyFeature(extracted)
    score = score_page_feature(query="купить велосипед", feature=feature)

    assert score.total_score > 0
    assert score.intent_fit >= 50
    print("smoke_test: OK")
    print(
        {
            "intent_fit": score.intent_fit,
            "structure_fit": score.structure_fit,
            "semantic_coverage": score.semantic_coverage,
            "commercial_fit": score.commercial_fit,
            "trust_fit": score.trust_fit,
            "total_score": score.total_score,
        }
    )


if __name__ == "__main__":
    main()
