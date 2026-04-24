from dataclasses import dataclass
from pathlib import Path

import httpx


@dataclass
class FetchResult:
    url: str
    fetch_status: str
    http_status: int | None
    raw_html_path: str | None
    error_message: str | None


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
        output_path.write_text(response.text, encoding="utf-8", errors="ignore")
        return FetchResult(
            url=url,
            fetch_status="ok" if response.status_code < 400 else "http_error",
            http_status=response.status_code,
            raw_html_path=str(output_path),
            error_message=None,
        )
    except Exception as exc:
        return FetchResult(
            url=url,
            fetch_status="fetch_error",
            http_status=None,
            raw_html_path=None,
            error_message=str(exc),
        )
