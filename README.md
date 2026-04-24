# SEO_ranker_min

Минимальный SEO-инструмент для быстрой проверки гипотезы ранжирования:
- сбор реального top-10 из Yandex через Playwright
- загрузка страниц конкурентов и целевой страницы
- извлечение признаков страниц
- внутренний scoring
- AI-уточнение (опционально)
- рекомендации по доработке target-страницы

## Текущий стек
- Python 3.11+
- FastAPI
- SQLAlchemy + SQLite
- Playwright
- BeautifulSoup4 + lxml
- httpx
- Jinja2
- OpenAI-compatible API

## Быстрый запуск
Рекомендуемый интерпретатор: **Python 3.11**.

1. Создать и активировать виртуальное окружение.
2. Установить зависимости:
   ```bash
   pip install -r requirements.txt
   ```
3. Установить браузер Playwright:
   ```bash
   playwright install chromium
   ```
4. Создать `.env` на основе `.env.example`.
5. Запустить приложение:
   ```bash
   uvicorn app.main:app --reload
   ```
6. Открыть: `http://127.0.0.1:8000`

## Новый формат ввода ключевых слов
Ключевые слова вводятся в `textarea` на главной странице.

Правила ввода:
- каждая строка — отдельный поисковый запрос
- также поддерживается разделение через запятую

Пример:
```text
обучение Python с нуля
курс Python онлайн
онлайн школа с ИИ
AI тренажер
проверка заданий с ИИ
```

Как это работает:
- система сохраняет каждое ключевое слово в таблицу `analysis_keywords`
- SERP собирается отдельно по каждому ключу
- AI/Codex рекомендации учитывают всю группу ключей, а не только один запрос
- `manual_top_urls` можно использовать как fallback, если Yandex возвращает `captcha` или `no_results`
- на странице результата ключевые слова показываются отдельным списком со статусами (`pending`, `running`, `done`, `failed`)
- блок Top-10 группируется по каждому keyword, а progress учитывает обработку всей группы

## Переменные окружения
```env
OPENAI_API_KEY=
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini
```

Если `OPENAI_API_KEY` пустой, приложение выполняет базовый анализ без AI.

## Что реализовано в MVP
- Форма запуска анализа: `keywords + target_url`
- Поддержка группы ключевых слов в одном запуске
- Ручной fallback top-10: можно вставить URL списком (по одному в строке) и запустить анализ без обращения к Yandex
- Ручной fallback применяется для всей группы ключевых слов и сохраняется отдельно по каждому `keyword_id`
- Сбор SERP top-10 из Yandex (desktop, Playwright)
- Сохранение артефактов выдачи:
  - `artifacts/runs/<run_id>/serp.html`
  - `artifacts/runs/<run_id>/serp.png`
- Загрузка страниц top-10 и целевой страницы
- Сохранение сырых HTML страниц в `artifacts/runs/<run_id>/pages/`
- Извлечение признаков страницы:
  - `title`, `meta description`, `h1`, `h2/h3`, текст
  - FAQ, таблицы, списки, CTA, формы, цены, отзывы, контакты
  - внутренние/внешние ссылки
  - schema.org
- Внутренний scoring (5 слоев + weighted sum)
- Внутренний рейтинг (`internal_rank`)
- AI-этап (SERP/page analyzer) с fallback
- Отдельный блок результата `SERP / Intent Summary` (dominant intent/type, required blocks, entities, expectations)
- Базовый gap-анализ и рекомендации по приоритетам
- Codex advice и AI-рекомендации по всей группе ключевых слов
- UI результата с группировкой SERP по keyword и отображением статусов ключевых слов

## Таблицы SQLite
- `analysis_runs`
- `analysis_keywords`
- `serp_results` — может быть связан с `keyword_id`
- `page_snapshots`
- `page_features`
- `page_scores`
- `recommendations`

## Тестовый сценарий
Локальный smoke-тест:
```bash
python scripts/smoke_test.py
```

## Отладочные артефакты запуска
Для каждого запуска создается папка:
`artifacts/runs/<run_id>/`

Ключевые файлы:
- `debug_log.jsonl` — пошаговый лог пайплайна в JSONL
- `debug_result_summary.json` — итоговый снимок статуса и счетчиков
- `serp_summary.json` — summary по intent/странице результата
- `codex_advice.json` — рекомендации AI-агента для следующих правок в коде/пайплайне

## Ограничения текущего MVP
- Только Yandex
- Только top-10
- Без очередей фоновых воркеров
- Без авторизации
- Без Search Console / Webmaster интеграций
- При срабатывании антибот-защиты Yandex запуск помечается как `captcha_detected`
