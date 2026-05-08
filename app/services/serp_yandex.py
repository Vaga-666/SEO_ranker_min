import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


@dataclass
class SerpItem:
    position: int
    url: str
    title: str
    snippet: str | None
    domain: str


@dataclass
class SerpFetchResult:
    items: list[SerpItem]
    html_path: str
    screenshot_path: str
    attempts: int
    note: str | None = None


class YandexCaptchaDetected(RuntimeError):
    def __init__(self, current_url: str, html_path: str, screenshot_path: str) -> None:
        super().__init__("Yandex captcha page detected")
        self.current_url = current_url
        self.html_path = html_path
        self.screenshot_path = screenshot_path


def fetch_yandex_top10(query: str, run_dir: Path) -> SerpFetchResult:
    _ensure_windows_proactor_policy()

    run_dir.mkdir(parents=True, exist_ok=True)
    html_path = run_dir / "serp.html"
    screenshot_path = run_dir / "serp.png"

    last_html = ""
    last_note: str | None = None
    max_attempts = 3

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 2000},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="ru-RU",
        )
        page = context.new_page()

        items: list[SerpItem] = []
        for attempt in range(1, max_attempts + 1):
            search_url = f"https://yandex.ru/search/?text={quote(query)}&p=0&ncrnd={attempt}"
            page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2200 + attempt * 600)

            try:
                page.wait_for_selector("li.serp-item, article, .Organic, a.Link", timeout=10000)
            except PlaywrightTimeoutError:
                pass

            html = page.content()
            last_html = html
            (run_dir / f"serp_attempt_{attempt}.html").write_text(html, encoding="utf-8")
            _safe_screenshot(page, run_dir / f"serp_attempt_{attempt}.png")

            current_url = page.url
            title = _safe_title(page)
            if is_yandex_captcha_page(html=html, current_url=current_url, title=title):
                html_path.write_text(html, encoding="utf-8")
                _safe_screenshot(page, screenshot_path)
                context.close()
                browser.close()
                raise YandexCaptchaDetected(
                    current_url=current_url,
                    html_path=str(html_path),
                    screenshot_path=str(screenshot_path),
                )

            items = _extract_items_from_dom(page)
            if len(items) < 3:
                items = _extract_items_from_html(html)

            if items:
                html_path.write_text(html, encoding="utf-8")
                _safe_screenshot(page, screenshot_path)
                context.close()
                browser.close()
                return SerpFetchResult(
                    items=items[:10],
                    html_path=str(html_path),
                    screenshot_path=str(screenshot_path),
                    attempts=attempt,
                    note=f"success_after_attempt_{attempt}",
                )

            if _is_no_results_page(html.lower()):
                last_note = "no_results_page_detected"
                break
            last_note = "empty_results_after_attempt"

        if last_html:
            html_path.write_text(last_html, encoding="utf-8")
            _safe_screenshot(page, screenshot_path)

        context.close()
        browser.close()

    return SerpFetchResult(
        items=[],
        html_path=str(html_path),
        screenshot_path=str(screenshot_path),
        attempts=max_attempts,
        note=last_note or "empty_results_after_retries",
    )


def _extract_items_from_dom(page) -> list[SerpItem]:
    items: list[SerpItem] = []
    seen: set[str] = set()
    selectors = ["li.serp-item", ".Organic", "article"]
    for sel in selectors:
        candidates = page.locator(sel)
        count = candidates.count()
        for idx in range(count):
            if len(items) >= 10:
                return items
            block = candidates.nth(idx)
            link = block.locator(
                "h2 a, a.Link_theme_normal, a.Link, a.OrganicTitle-Link, a.organic__url"
            )
            if link.count() == 0:
                continue

            href = (link.first.get_attribute("href") or "").strip()
            if not _is_result_url(href):
                continue

            key = href.rstrip("/")
            if key in seen:
                continue
            seen.add(key)

            title = (link.first.inner_text() or "").strip()
            if len(title) < 4:
                continue

            snippet = None
            snippet_locator = block.locator(
                ".OrganicTextContentSpan, .text-container, .ExtendedText, .TextContainer"
            )
            if snippet_locator.count() > 0:
                raw = (snippet_locator.first.inner_text() or "").strip()
                snippet = raw or None

            domain = urlparse(href).netloc.lower().replace("www.", "")
            items.append(
                SerpItem(
                    position=len(items) + 1,
                    url=href,
                    title=title,
                    snippet=snippet,
                    domain=domain,
                )
            )
    return items


def _extract_items_from_html(html: str) -> list[SerpItem]:
    soup = BeautifulSoup(html, "lxml")
    items: list[SerpItem] = []
    seen: set[str] = set()

    for a_tag in soup.select("a[href]"):
        if len(items) >= 10:
            break
        href = (a_tag.get("href") or "").strip()
        if not _is_result_url(href):
            continue

        title = a_tag.get_text(" ", strip=True)
        if len(title) < 4:
            continue

        key = href.rstrip("/")
        if key in seen:
            continue
        seen.add(key)

        domain = urlparse(href).netloc.lower().replace("www.", "")
        items.append(
            SerpItem(
                position=len(items) + 1,
                url=href,
                title=title[:300],
                snippet=None,
                domain=domain,
            )
        )

    return items


def _is_result_url(href: str) -> bool:
    if not href:
        return False
    if href.startswith(("#", "/", "javascript:", "mailto:", "tel:")):
        return False

    parsed = urlparse(href)
    if parsed.scheme not in {"http", "https"}:
        return False

    domain = parsed.netloc.lower().replace("www.", "")
    blocked = {
        "yandex.ru",
        "ya.ru",
        "passport.yandex.ru",
        "google.com",
        "bing.com",
    }
    if any(domain == b or domain.endswith("." + b) for b in blocked):
        return False
    return True


def _safe_screenshot(page, path: Path) -> None:
    try:
        page.screenshot(path=str(path), full_page=True, timeout=10000)
    except Exception:
        return


def _safe_title(page) -> str:
    try:
        return page.title()
    except Exception:
        return ""


def is_yandex_captcha_page(html: str, current_url: str = "", title: str = "") -> bool:
    lower_html = (html or "").lower()
    lower_url = (current_url or "").lower()
    lower_title = (title or "").lower()
    page_text = BeautifulSoup(html or "", "lxml").get_text(" ", strip=True).lower()
    return any(
        [
            "/showcaptcha" in lower_url,
            "\u0432\u044b \u043d\u0435 \u0440\u043e\u0431\u043e\u0442" in page_text,
            "\u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u0435, \u0447\u0442\u043e \u0437\u0430\u043f\u0440\u043e\u0441\u044b \u043e\u0442\u043f\u0440\u0430\u0432\u043b\u044f\u043b\u0438 \u0432\u044b" in page_text,
            "\u0432\u044b \u043d\u0435 \u0440\u043e\u0431\u043e\u0442" in lower_title,
            "captcha" in lower_html and "<form" in lower_html,
            "showcaptcha" in lower_html,
        ]
    )


def _is_no_results_page(lower_html: str) -> bool:
    return (
        "\u043d\u0438\u0447\u0435\u0433\u043e \u043d\u0435 \u043d\u0430\u0448\u043b\u0438" in lower_html
        or "\u043d\u0438\u0447\u0435\u0433\u043e \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u043e" in lower_html
        or "\u043f\u043e \u0432\u0430\u0448\u0435\u043c\u0443 \u0437\u0430\u043f\u0440\u043e\u0441\u0443 \u043d\u0438\u0447\u0435\u0433\u043e \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u043e" in lower_html
    )


def _ensure_windows_proactor_policy() -> None:
    if not sys.platform.startswith("win"):
        return
    policy = asyncio.get_event_loop_policy()
    if policy.__class__.__name__ != "WindowsProactorEventLoopPolicy":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
