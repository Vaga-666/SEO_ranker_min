from dataclasses import dataclass
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


@dataclass
class FetchResult:
    url: str
    fetch_status: str
    http_status: int | None
    raw_html_path: str | None
    error_message: str | None
    fetch_method: str = "httpx"


def fetch_page_html(url: str, output_path: Path, timeout_sec: int = 25) -> FetchResult:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    try:
        with httpx.Client(
            timeout=timeout_sec,
            follow_redirects=True,
            headers=headers,
            verify=False,
        ) as client:
            response = client.get(url)
        html = response.text
        fetch_method = "httpx"
        rendered = None
        if _looks_like_react_shell(html):
            try:
                rendered = _fetch_rendered_html(url=url, timeout_sec=timeout_sec)
                if rendered:
                    html = rendered.html
                    fetch_method = "playwright_rendered"
            except Exception:
                rendered = None
        if fetch_method == "playwright_rendered":
            html = "<!-- data-seo-ranker-rendered: playwright_rendered -->\n" + html

        output_path.write_text(html, encoding="utf-8", errors="ignore")
        return FetchResult(
            url=url,
            fetch_status="ok" if response.status_code < 400 else "http_error",
            http_status=rendered.http_status if fetch_method == "playwright_rendered" and rendered else response.status_code,
            raw_html_path=str(output_path),
            error_message=None,
            fetch_method=fetch_method,
        )
    except Exception as exc:
        return FetchResult(
            url=url,
            fetch_status="fetch_error",
            http_status=None,
            raw_html_path=None,
            error_message=str(exc),
            fetch_method="httpx",
        )


@dataclass
class _RenderedResult:
    html: str
    http_status: int | None


def _fetch_rendered_html(url: str, timeout_sec: int) -> _RenderedResult | None:
    timeout_ms = max(5000, timeout_sec * 1000)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="ru-RU",
        )
        response = None
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 15000))
            except PlaywrightTimeoutError:
                # SPA pages often keep analytics/network activity alive; content() is still useful.
                pass
            try:
                page.wait_for_timeout(1500)
            except PlaywrightTimeoutError:
                pass
            return _RenderedResult(html=page.content(), http_status=response.status if response else None)
        finally:
            browser.close()


def _looks_like_react_shell(html: str) -> bool:
    if not html or len(html) < 100:
        return False
    soup = BeautifulSoup(html, "lxml")
    body = soup.body
    if body is None:
        return False
    for bad in soup(["script", "style", "noscript"]):
        bad.decompose()
    text = body.get_text(" ", strip=True)
    root = body.find(id="root")
    has_root = root is not None
    has_headings = bool(body.find(["h1", "h2", "h3"]))
    return has_root and len(text) < 250 and not has_headings
