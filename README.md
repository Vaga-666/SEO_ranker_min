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

## Переменные окружения
```env
OPENAI_API_KEY=
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini
```

Если `OPENAI_API_KEY` пустой, приложение выполняет базовый анализ без AI.

## Что реализовано в MVP
- Форма запуска анализа: `query + target_url`
- Ручной fallback top-10: можно вставить URL списком (по одному в строке) и запустить анализ без обращения к Yandex
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

## Таблицы SQLite
- `analysis_runs`
- `serp_results`
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
