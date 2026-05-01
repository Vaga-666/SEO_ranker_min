from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.feature_extractor import extract_page_features
from app.services.scoring import score_page_feature, score_page_feature_debug


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
        self.has_lead_form = extracted.has_lead_form
        self.has_prices = extracted.has_prices
        self.has_courses = extracted.has_courses
        self.has_reviews = extracted.has_reviews
        self.has_contacts = extracted.has_contacts
        self.internal_links_count = extracted.internal_links_count
        self.external_links_count = extracted.external_links_count
        self.has_schema_org = extracted.has_schema_org


def main() -> None:
    html = """
    <html>
      <head>
        <title>ABI studio - онлайн-школа с ИИ и практическими заданиями</title>
        <meta name="description" content="Онлайн-курсы, практические задания, AI Экзаменатор, отзывы, контакты и бесплатный старт">
        <script type="application/ld+json">
          {
            "@context": "https://schema.org",
            "@graph": [
              {"@type": "Organization", "name": "ABI studio", "email": "support@bots-ai.net"},
              {"@type": "WebPage", "name": "Онлайн-школа ABI studio"},
              {
                "@type": "FAQPage",
                "mainEntity": [
                  {
                    "@type": "Question",
                    "name": "Можно ли начать бесплатно?",
                    "acceptedAnswer": {"@type": "Answer", "text": "Да, можно выбрать бесплатный старт."}
                  }
                ]
              }
            ]
          }
        </script>
      </head>
      <body>
        <h1>Онлайн-школа с ИИ, практическими заданиями и AI-тренажером</h1>
        <h2>Курсы и форматы обучения</h2>
        <ul>
          <li>Бесплатные курсы</li>
          <li>Платные практические курсы</li>
          <li>Курс Python онлайн</li>
          <li>SQL и обучение программированию</li>
        </ul>
        <a href="#courses">Смотреть курсы</a>
        <button>Начать бесплатно</button>
        <a href="mailto:support@bots-ai.net">Задать вопрос</a>

        <section>
          <h2>Нужна помощь с выбором курса?</h2>
          <form action="mailto:support@bots-ai.net" method="post" enctype="text/plain">
            <label>Ваше имя<input name="name" placeholder="Ваше имя" /></label>
            <label>Контакт<input name="contact" placeholder="Email или Telegram" /></label>
            <label>Сообщение<textarea name="message" placeholder="Какой курс или формат обучения вас интересует?"></textarea></label>
            <button type="submit">Отправить заявку</button>
          </form>
        </section>

        <section>
          <h2>Вопрос-ответ</h2>
          <h3>Что такое ABI studio?</h3>
          <p>Это образовательная платформа и онлайн обучение через практические задания.</p>
          <h3>Можно ли начать бесплатно?</h3>
          <p>Да, можно открыть бесплатные курсы и затем купить платные форматы обучения.</p>
        </section>

        <section>
          <h2>Отзывы и результаты</h2>
          <p>Ученик, студент, пользователь и преподаватель делятся учебными результатами и кейсами.</p>
        </section>

        <section>
          <h2>Контакты</h2>
          <p>Email: support@bots-ai.net</p>
          <p>Сайт и формат работы: онлайн.</p>
        </section>
      </body>
    </html>
    """
    extracted = extract_page_features(html=html, page_url="https://example.com/")
    feature = _DummyFeature(extracted)
    score = score_page_feature(query="онлайн школа", feature=feature)
    debug = score_page_feature_debug(query="онлайн школа", feature=feature)

    assert score.total_score > 0
    assert extracted.has_cta is True
    assert extracted.has_forms is True
    assert extracted.has_lead_form is True
    assert extracted.has_reviews is True
    assert extracted.has_contacts is True
    assert extracted.has_faq is True
    assert extracted.has_schema_org is True
    assert extracted.has_courses is True
    assert score.structure_fit > 0
    assert score.commercial_fit > 0
    assert score.trust_fit > 0
    assert debug.reasons["structure_fit"]["passed_rules"]
    assert debug.reasons["commercial_fit"]["passed_rules"]
    assert debug.reasons["trust_fit"]["passed_rules"]

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
