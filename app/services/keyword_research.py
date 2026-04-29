import asyncio
import json
import random
import re
import sys
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup
from openai import OpenAI
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from app.config import get_settings


class KeywordDiscoveryError(Exception):
    pass


class KeywordDiscoveryLogger:
    def __init__(self, request_id: str):
        self.request_id = request_id
        log_dir = Path("artifacts") / "keyword_discovery"
        log_dir.mkdir(parents=True, exist_ok=True)
        self.path = log_dir / f"{request_id}.jsonl"

    def log(self, event: str, **data: Any) -> None:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "request_id": self.request_id,
            "event": event,
            "data": data,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def discover_keywords(
    target_url: str = "",
    topic_hint: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    rid = request_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    logger = KeywordDiscoveryLogger(rid)

    settings = get_settings()
    topic = (topic_hint or "").strip()
    target = (target_url or "").strip()
    logger.log("start", target_url=target, topic_hint=topic, has_openai_key=bool(settings.openai_api_key))
    logger.log("mode", type="strict_single_query_cycle")

    page_context = _fetch_target_context(target, logger) if target else {"title": "", "h1": "", "description": "", "text": ""}

    if not topic:
        if not settings.openai_api_key:
            raise KeywordDiscoveryError("Автоопределение темы требует OPENAI_API_KEY.")
        if not target:
            raise KeywordDiscoveryError("Для автоопределения темы нужен target URL.")
        topic = _infer_topic_with_ai(target_url=target, page_context=page_context, logger=logger)
        if not topic:
            raise KeywordDiscoveryError("AI не смог определить тему по target-странице.")
    logger.log("topic.resolved", topic=topic)

    max_iterations = 1
    logger.log("loop.config", max_iterations=max_iterations)

    diagnostics: dict[str, Any] = {
        "wordstat_attempts": 0,
        "wordstat_successes": 0,
        "cycles_completed": 0,
        "auth_required_detected": False,
        "captcha_detected": False,
    }

    phrases_by_seed: dict[str, list[dict[str, Any]]] = {}
    all_phrases: OrderedDict[str, dict[str, Any]] = OrderedDict()
    seed_queries: list[str] = []
    previous_seeds: list[str] = []

    for index in range(1, max_iterations + 1):
        seed = _generate_next_seed_query(
            topic=topic,
            target_url=target,
            page_context=page_context,
            previous_seeds=previous_seeds,
            collected_phrases=[v.get("phrase", "") for v in all_phrases.values()],
            logger=logger,
        )
        if not seed:
            logger.log("loop.stop", reason="ai_empty_next_seed", iteration=index)
            break

        previous_seeds.append(seed)
        seed_queries.append(seed)
        diagnostics["wordstat_attempts"] += 1

        logger.log("cycle.start", index=index, seed=seed)
        t0 = time.perf_counter()
        parsed_items, fetch_meta = _fetch_wordstat_items(seed_query=seed, logger=logger)
        if fetch_meta.get("auth_required"):
            diagnostics["auth_required_detected"] = True
        if fetch_meta.get("captcha"):
            diagnostics["captcha_detected"] = True
        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        if parsed_items:
            diagnostics["wordstat_successes"] += 1
        diagnostics["cycles_completed"] += 1

        deduped = _dedupe_items(parsed_items)[:25]
        phrases_by_seed[seed] = deduped
        for item in deduped:
            key = str(item.get("phrase", "")).strip().lower()
            if not key:
                continue
            current = all_phrases.get(key)
            if current is None:
                all_phrases[key] = item
                continue
            prev_count = current.get("count")
            next_count = item.get("count")
            if isinstance(next_count, int) and (not isinstance(prev_count, int) or next_count > prev_count):
                all_phrases[key] = item

        logger.log(
            "cycle.done",
            index=index,
            seed=seed,
            elapsed_ms=elapsed_ms,
            raw_count=len(parsed_items),
            kept_count=len(deduped),
        )

        if index < max_iterations:
            delay = round(random.uniform(2.0, 3.8), 2)
            logger.log("cycle.sleep", index=index, seconds=delay)
            time.sleep(delay)

    keywords_with_counts = list(all_phrases.values())[:150]
    if not keywords_with_counts and diagnostics["auth_required_detected"]:
        raise KeywordDiscoveryError(
            "Wordstat не отдает данные в автоматической сессии: требуется авторизация в Яндексе."
        )
    if not keywords_with_counts and diagnostics["captcha_detected"]:
        raise KeywordDiscoveryError(
            "Wordstat вернул антибот/капчу. Требуется интерактивный вход и ручное подтверждение."
        )
    if not keywords_with_counts:
        raise KeywordDiscoveryError(
            f"Wordstat не вернул фразы. Попыток: {diagnostics['wordstat_attempts']}, успешных: {diagnostics['wordstat_successes']}."
        )
    keywords = [str(item.get("phrase", "")).strip() for item in keywords_with_counts if str(item.get("phrase", "")).strip()]
    elapsed_total_ms = int((time.perf_counter() - started) * 1000)

    result = {
        "request_id": rid,
        "topic": topic,
        "seed_queries": seed_queries,
        "wordstat_results": phrases_by_seed,
        "keywords": keywords,
        "keywords_with_counts": keywords_with_counts,
        "diagnostics": diagnostics,
        "log_file": str(logger.path).replace("\\", "/"),
    }
    logger.log("finish", elapsed_total_ms=elapsed_total_ms, keywords_count=len(keywords), diagnostics=diagnostics)
    return result


def _fetch_target_context(target_url: str, logger: KeywordDiscoveryLogger) -> dict[str, str]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    try:
        t0 = time.perf_counter()
        with httpx.Client(timeout=20, follow_redirects=True, headers=headers, verify=False) as client:
            response = client.get(target_url)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        soup = BeautifulSoup(response.text, "lxml")
        title = (soup.title.get_text(" ", strip=True) if soup.title else "").strip()
        h1_tag = soup.find("h1")
        h1 = (h1_tag.get_text(" ", strip=True) if h1_tag else "").strip()
        meta_tag = soup.find("meta", attrs={"name": "description"})
        description = (meta_tag.get("content", "") if meta_tag else "").strip()
        text = " ".join(soup.stripped_strings)
        text = re.sub(r"\s+", " ", text).strip()[:10000]

        course_candidates = _extract_course_candidates(soup)
        logger.log(
            "target.fetch.ok",
            elapsed_ms=elapsed_ms,
            status_code=response.status_code,
            title=title[:120],
            course_candidates_count=len(course_candidates),
        )
        return {
            "title": title,
            "h1": h1,
            "description": description,
            "text": text,
            "course_candidates": json.dumps(course_candidates[:80], ensure_ascii=False),
        }
    except Exception as exc:
        logger.log("target.fetch.error", error=f"{exc.__class__.__name__}: {str(exc) or repr(exc)}")
        return {"title": "", "h1": "", "description": "", "text": "", "course_candidates": "[]"}


def _extract_course_candidates(soup: BeautifulSoup) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        text = re.sub(r"\s+", " ", (raw or "").strip())
        if len(text) < 8 or len(text) > 140:
            return
        low = text.lower()
        if low in seen:
            return
        if not any(ch.isalpha() for ch in low):
            return
        noise_tokens = ("войти", "регистрация", "политика", "cookie", "подробнее", "читать далее")
        if any(tok in low for tok in noise_tokens):
            return
        seen.add(low)
        candidates.append(text)

    for tag in soup.select("h1, h2, h3, .course, .courses, .program, .programs, [class*='course'], [class*='program']"):
        add(tag.get_text(" ", strip=True))

    for a in soup.select("a[href]"):
        href = (a.get("href") or "").lower()
        if any(x in href for x in ("/course", "/courses", "/program", "/programs", "/catalog", "/kurs", "/kursy")):
            add(a.get_text(" ", strip=True))

    return candidates[:120]


def _infer_topic_with_ai(target_url: str, page_context: dict[str, str], logger: KeywordDiscoveryLogger) -> str:
    settings = get_settings()
    client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)
    payload = {
        "task": "Determine commercial SEO topic(s) of this site for Yandex Wordstat based on real offerings, especially courses/programs.",
        "target_url": target_url,
        "page": {
            "title": page_context.get("title", ""),
            "h1": page_context.get("h1", ""),
            "description": page_context.get("description", ""),
            "text_excerpt": page_context.get("text", "")[:6000],
            "course_candidates": page_context.get("course_candidates", "[]"),
        },
        "rules": [
            "Do not use generic page title as final topic if content indicates more specific offerings.",
            "Prioritize recurring course/program entities and audience segments.",
            "Output in Russian.",
        ],
        "output_schema": {
            "primary_topic": "string, 3-8 words, specific commercial topic",
            "secondary_topics": ["string", "string", "string"],
            "rationale_short": "string"
        },
    }
    try:
        t0 = time.perf_counter()
        response = client.chat.completions.create(
            model=settings.openai_model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "Return JSON only."},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0.1,
        )
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        parsed = json.loads(response.choices[0].message.content or "{}")
        primary = str(parsed.get("primary_topic", "")).strip()
        secondary = parsed.get("secondary_topics", [])
        if not isinstance(secondary, list):
            secondary = []
        secondary_clean = [str(x).strip() for x in secondary if str(x).strip()]
        topic = primary or (secondary_clean[0] if secondary_clean else "")
        logger.log("ai.topic.ok", elapsed_ms=elapsed_ms, topic=topic, secondary_topics=secondary_clean[:3])
        return topic
    except Exception as exc:
        logger.log("ai.topic.error", error=f"{exc.__class__.__name__}: {str(exc) or repr(exc)}")
        return ""


def _generate_next_seed_query(
    topic: str,
    target_url: str,
    page_context: dict[str, str],
    previous_seeds: list[str],
    collected_phrases: list[str],
    logger: KeywordDiscoveryLogger,
) -> str:
    settings = get_settings()
    if not settings.openai_api_key:
        return ""
    client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)
    payload = {
        "task": "Generate ONE next seed query for Yandex Wordstat.",
        "topic": topic,
        "target_url": target_url,
        "page": {
            "title": page_context.get("title", ""),
            "h1": page_context.get("h1", ""),
            "description": page_context.get("description", ""),
        },
        "already_used_seed_queries": previous_seeds[-30:],
        "already_collected_phrases_sample": collected_phrases[-80:],
        "requirements": [
            "Return exactly one query string.",
            "Do not repeat previously used seed queries.",
            "Query must be realistic and searchable in Yandex.",
            "Prefer Russian market wording when applicable.",
        ],
        "output_schema": {"query": "string"},
    }
    try:
        t0 = time.perf_counter()
        response = client.chat.completions.create(
            model=settings.openai_model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "Return JSON only."},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0.2,
        )
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        parsed = json.loads(response.choices[0].message.content or "{}")
        query = str(parsed.get("query", "")).strip()
        if not query:
            logger.log("ai.seed.error", elapsed_ms=elapsed_ms, reason="empty_query")
            return ""
        if query in previous_seeds:
            logger.log("ai.seed.error", elapsed_ms=elapsed_ms, reason="duplicate_query", query=query)
            return ""
        logger.log("ai.seed.ok", elapsed_ms=elapsed_ms, query=query)
        return query
    except Exception as exc:
        logger.log("ai.seed.error", error=f"{exc.__class__.__name__}: {str(exc) or repr(exc)}")
        return ""


def _fetch_wordstat_items(seed_query: str, logger: KeywordDiscoveryLogger) -> tuple[list[dict[str, Any]], dict[str, bool]]:
    _ensure_windows_proactor_policy()
    url = f"https://wordstat.yandex.ru/?words={quote_plus(seed_query)}"
    html = ""
    final_url = ""
    page_title = ""
    network_items: list[dict[str, Any]] = []
    meta = {"auth_required": False, "captcha": False}
    profile_dir = Path("artifacts") / "playwright_wordstat_profile"
    profile_dir.mkdir(parents=True, exist_ok=True)

    def run_session(headless: bool, manual_login_wait: bool) -> tuple[str, str, str, list[dict[str, Any]]]:
        local_html = ""
        local_final_url = ""
        local_title = ""
        local_items: list[dict[str, Any]] = []
        with sync_playwright() as p:
            launch_kwargs: dict[str, Any] = {
                "user_data_dir": str(profile_dir),
                "headless": headless,
                "locale": "ru-RU",
                "user_agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
            }
            if manual_login_wait:
                launch_kwargs["no_viewport"] = True
                launch_kwargs["args"] = ["--start-maximized"]
            else:
                launch_kwargs["viewport"] = {"width": 1440, "height": 2000}

            context = p.chromium.launch_persistent_context(
                **launch_kwargs,
            )
            page = context.new_page()

            def _on_response(resp):
                try:
                    ctype = (resp.headers.get("content-type") or "").lower()
                    if "json" not in ctype:
                        return
                    body = resp.text()
                    parsed = json.loads(body)
                    extracted: list[dict[str, Any]] = []
                    if "/wordstat/api/search" in resp.url:
                        extracted = _extract_wordstat_search_table(parsed)
                    if not extracted:
                        if not any(k in body for k in ['"shows"', '"phrase"', '"text"', '"requests"', '"freq"', '"words"', '"value"']):
                            return
                        extracted = _extract_phrase_counts_from_json(parsed)
                    if extracted:
                        local_items.extend(extracted)
                except Exception:
                    return

            page.on("response", _on_response)
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            if manual_login_wait:
                deadline = time.time() + 240
                attempt = 0
                logger.log("wordstat.manual_login.wait_start", timeout_sec=240)
                while time.time() < deadline:
                    attempt += 1
                    _ensure_login_form_visible(page)
                    page.wait_for_timeout(2500)
                    cur_url = page.url.lower()
                    if "passport.yandex" in cur_url or "id.yandex" in cur_url:
                        continue
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=45000)
                        page.wait_for_timeout(1200)
                    except Exception:
                        pass
                    cur_url = page.url.lower()
                    if "passport.yandex" not in cur_url and "id.yandex" not in cur_url:
                        logger.log("wordstat.manual_login.done", attempts=attempt, url=page.url)
                        break
                else:
                    logger.log("wordstat.manual_login.timeout")
            try:
                page.wait_for_timeout(2200)
                page.wait_for_load_state("networkidle", timeout=3500)
            except PlaywrightTimeoutError:
                logger.log("wordstat.open.networkidle_timeout", seed=seed_query, url=url, headless=headless)
            local_final_url = page.url
            local_title = page.title()
            local_html = page.content()
            context.close()
        return local_html, local_final_url, local_title, local_items

    try:
        logger.log("wordstat.open.start", seed=seed_query, url=url, headless=True)
        html, final_url, page_title, network_items = run_session(headless=True, manual_login_wait=False)
        logger.log("wordstat.open.ok", seed=seed_query, url=url, final_url=final_url, title=page_title, html_len=len(html))
    except Exception as exc:
        logger.log("wordstat.open.error", seed=seed_query, url=url, error=f"{exc.__class__.__name__}: {str(exc) or repr(exc)}")
        return [], meta

    if network_items:
        deduped_network = _dedupe_items(network_items)
        logger.log("wordstat.network.items", seed=seed_query, count=len(deduped_network))
        if deduped_network:
            return deduped_network, meta

    if not html:
        logger.log("wordstat.parse.empty_html", seed=seed_query)
        return [], meta

    soup = BeautifulSoup(html, "lxml")
    page_text = soup.get_text(" ", strip=True).lower()
    title_text = (soup.title.get_text(" ", strip=True).lower() if soup.title else "")
    html_low = html.lower()
    final_url_low = final_url.lower()

    if "вы не робот" in page_text or "captcha" in page_text or "smartcaptcha" in html_low:
        logger.log("wordstat.parse.captcha", seed=seed_query, final_url=final_url, title=title_text[:120])
        meta["captcha"] = True
        return [], meta

    if (
        "you must be logged in to use wordstat" in page_text
        or "авторизация" in title_text
        or "вход" in title_text
        or "passport.yandex" in final_url_low
        or "id.yandex" in final_url_low
        or ("войдите" in page_text and "wordstat" in page_text)
        or "вход в аккаунт" in page_text
    ):
        logger.log("wordstat.parse.auth_required", seed=seed_query, final_url=final_url, title=title_text[:120])
        meta["auth_required"] = True
        try:
            logger.log("wordstat.open.retry_manual_login.start", seed=seed_query, url=url)
            html, final_url, page_title, second_network_items = run_session(headless=False, manual_login_wait=True)
            if second_network_items:
                network_items.extend(second_network_items)
            logger.log(
                "wordstat.open.retry_manual_login.done",
                seed=seed_query,
                final_url=final_url,
                title=page_title,
                html_len=len(html),
            )
            meta["auth_required"] = False
        except Exception as exc:
            logger.log(
                "wordstat.open.retry_manual_login.error",
                seed=seed_query,
                error=f"{exc.__class__.__name__}: {str(exc) or repr(exc)}",
            )
            return [], meta

    items: list[dict[str, Any]] = []
    selectors = [
        "[data-testid='wordstat__phrase-text']",
        "[data-testid='phrase-text']",
        ".phrase__text",
        ".wordstat-phrase",
        ".wordstat__table a",
        ".wordstat__table td",
    ]
    for selector in selectors:
        for node in soup.select(selector):
            phrase = re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
            if not _valid_phrase(phrase):
                continue
            count_value = _extract_count_from_node(node)
            items.append({"phrase": phrase, "count": count_value, "source": "wordstat_dom"})
        if len(items) >= 10:
            logger.log("wordstat.parse.selector_hit", seed=seed_query, selector=selector, count=len(items))
            break

    if len(items) < 5:
        logger.log("wordstat.parse.low_count", seed=seed_query, count=len(items), final_url=final_url, title=title_text[:120])

    return _dedupe_items(items), meta
def _extract_phrase_counts_from_json(payload: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            phrase = None
            count = None

            for k in ("phrase", "text", "query", "words", "word"):
                v = value.get(k)
                if isinstance(v, str) and _valid_phrase(v):
                    phrase = v.strip()
                    break

            for k in ("shows", "count", "requests", "freq", "frequency", "hits", "impressions", "value"):
                v = value.get(k)
                if isinstance(v, int):
                    count = v
                    break
                if isinstance(v, str):
                    digits = re.sub(r"\D", "", v)
                    if digits.isdigit():
                        count = int(digits)
                        break

            if phrase and isinstance(count, int):
                items.append({"phrase": phrase, "count": count, "source": "wordstat_json"})

            for child in value.values():
                walk(child)
            return

        if isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    return items


def _extract_wordstat_search_table(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    table = payload.get("table")
    if not isinstance(table, dict):
        return []
    table_data = table.get("tableData")
    if not isinstance(table_data, dict):
        return []

    items: list[dict[str, Any]] = []
    for bucket in ("popular",):
        rows = table_data.get(bucket)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            phrase = str(row.get("text", "")).strip()
            if not _valid_phrase(phrase):
                continue
            if _looks_like_month_or_region_row(phrase):
                continue
            count = _parse_int(row.get("value"))
            items.append(
                {
                    "phrase": phrase,
                    "count": count,
                    "source": f"wordstat_api_{bucket}",
                }
            )
    return _dedupe_items(items)


def _parse_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        digits = re.sub(r"\D", "", value)
        if digits.isdigit():
            return int(digits)
    return None


def _looks_like_month_or_region_row(phrase: str) -> bool:
    low = " ".join(phrase.lower().split())
    if not low:
        return True
    # conservative filter: only obvious region summary rows
    if low.endswith(" ? ???????") or low.endswith(" region"):
        return True
    return False

def _extract_count_from_node(node) -> int | None:
    for holder in [node, node.parent, node.parent.parent if node.parent else None]:
        if holder is None:
            continue
        row_text = re.sub(r"\s+", " ", holder.get_text(" ", strip=True))
        m = re.search(r"(\d[\d\s]{1,15})\s*$", row_text)
        if not m:
            continue
        digits = re.sub(r"\D", "", m.group(1))
        if digits.isdigit():
            return int(digits)
    return None


def _dedupe_items(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in values:
        phrase = str(item.get("phrase", "")).strip()
        if not phrase:
            continue
        key = re.sub(r"\s+", " ", phrase.lower())
        if key not in merged:
            merged[key] = {"phrase": phrase, "count": item.get("count"), "source": item.get("source")}
            order.append(key)
            continue
        prev = merged[key]
        prev_count = prev.get("count")
        next_count = item.get("count")
        if isinstance(next_count, int) and (not isinstance(prev_count, int) or next_count > prev_count):
            prev["count"] = next_count
    return [merged[k] for k in order]


def _valid_phrase(text: str) -> bool:
    low = text.lower().strip()
    if not low:
        return False
    if len(low) < 3 or len(low) > 120:
        return False
    if low.isdigit():
        return False
    if "wordstat" in low:
        return False
    return any(ch.isalpha() for ch in low)

def _ensure_windows_proactor_policy() -> None:
    if not sys.platform.startswith("win"):
        return
    policy = asyncio.get_event_loop_policy()
    if policy.__class__.__name__ != "WindowsProactorEventLoopPolicy":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


def _ensure_login_form_visible(page) -> None:
    try:
        page.evaluate("window.scrollTo(0, 0)")
    except Exception:
        return

    try:
        page.evaluate(
            """
            () => {
              const pick = () => {
                const selectors = [
                  "input[name='login']",
                  "input[name='username']",
                  "#passp-field-login",
                  "input[type='password']",
                  "#passp-field-passwd",
                  "input"
                ];
                for (const s of selectors) {
                  const el = document.querySelector(s);
                  if (el) return el;
                }
                return null;
              };
              const el = pick();
              if (!el) return;
              el.scrollIntoView({ behavior: "instant", block: "center", inline: "nearest" });
              try { el.focus(); } catch (e) {}
              window.scrollBy(0, -120);
            }
            """
        )
    except Exception:
        pass
