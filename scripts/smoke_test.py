from pathlib import Path
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import Base, ensure_sqlite_schema
from app.services.feature_extractor import extract_page_features
from app.services.keyword_parser import parse_keywords
from app.models import AnalysisKeyword, AnalysisRun, SerpResult
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
    assert parse_keywords("обучение Python с нуля\nкурс Python онлайн") == [
        "обучение Python с нуля",
        "курс Python онлайн",
    ]
    assert parse_keywords("онлайн школа, обучение с ИИ, онлайн школа") == [
        "онлайн школа",
        "обучение с ИИ",
    ]
    assert parse_keywords("  ключ 1  \n\n ключ 2 , ключ 1 ,   ") == [
        "ключ 1",
        "ключ 2",
    ]
    assert parse_keywords("") == []

    test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=test_engine)
    ensure_sqlite_schema(test_engine)

    with TestingSessionLocal() as db:
        run = AnalysisRun(query="ключ 1\nключ 2", target_url="https://example.com", status="created")
        db.add(run)
        db.commit()
        db.refresh(run)

        db.add_all(
            [
                AnalysisKeyword(run_id=run.id, keyword="ключ 1"),
                AnalysisKeyword(run_id=run.id, keyword="ключ 2"),
            ]
        )
        db.commit()

        saved_keywords = (
            db.query(AnalysisKeyword)
            .filter(AnalysisKeyword.run_id == run.id)
            .order_by(AnalysisKeyword.id.asc())
            .all()
        )
        assert [item.keyword for item in saved_keywords] == ["ключ 1", "ключ 2"]

        serp_result = SerpResult(
            analysis_run_id=run.id,
            keyword_id=saved_keywords[0].id,
            position=1,
            url="https://example.com/result",
            title="Example result",
            snippet="Example snippet",
            domain="example.com",
            is_target=True,
        )
        db.add(serp_result)
        db.commit()
        db.refresh(serp_result)

        assert serp_result.keyword_id == saved_keywords[0].id

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
