import json
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup


@dataclass
class ExtractedFeatures:
    title: str | None
    meta_description: str | None
    h1: str | None
    h2_h3_json: str
    main_text: str | None
    has_faq: bool
    has_tables: bool
    has_lists: bool
    has_cta: bool
    has_forms: bool
    has_prices: bool
    has_reviews: bool
    has_contacts: bool
    internal_links_count: int
    external_links_count: int
    has_schema_org: bool


def extract_page_features(html: str, page_url: str) -> ExtractedFeatures:
    soup = BeautifulSoup(html, "lxml")
    host = urlparse(page_url).netloc.lower().replace("www.", "")

    title = _text_or_none(soup.title.string if soup.title else None)
    meta_description_tag = soup.find("meta", attrs={"name": "description"})
    meta_description = _text_or_none(meta_description_tag.get("content") if meta_description_tag else None)

    h1_tag = soup.find("h1")
    h1 = _text_or_none(h1_tag.get_text(" ", strip=True) if h1_tag else None)

    h2_h3 = [h.get_text(" ", strip=True) for h in soup.select("h2, h3")]
    h2_h3_json = json.dumps([t for t in h2_h3 if t], ensure_ascii=False)

    for bad in soup(["script", "style", "noscript"]):
        bad.decompose()
    text_chunks = [t.strip() for t in soup.stripped_strings]
    main_text = _text_or_none(" ".join(text_chunks[:1500]))

    page_text = (main_text or "").lower()
    has_faq = "faq" in page_text or "часто задаваем" in page_text
    has_tables = bool(soup.find("table"))
    has_lists = bool(soup.find("ul") or soup.find("ol"))
    has_forms = bool(soup.find("form"))
    has_schema_org = bool(soup.find(attrs={"itemtype": re.compile(r"schema\.org", re.IGNORECASE)}))
    has_cta = any(
        token in page_text
        for token in ["купить", "заказать", "оставить заявку", "подписаться", "получить", "связаться"]
    )
    has_prices = bool(re.search(r"\b\d[\d\s]*(?:₽|руб|rub|\$|€|eur|usd)\b", page_text, re.IGNORECASE))
    has_reviews = any(token in page_text for token in ["отзыв", "reviews", "рейтинг"])
    has_contacts = any(
        token in page_text for token in ["контакт", "телефон", "email", "e-mail", "адрес", "whatsapp", "telegram"]
    )

    internal_links_count = 0
    external_links_count = 0
    for a_tag in soup.find_all("a", href=True):
        href = a_tag.get("href", "").strip()
        if not href or href.startswith("#") or href.startswith("javascript:"):
            continue
        abs_url = urljoin(page_url, href)
        link_host = urlparse(abs_url).netloc.lower().replace("www.", "")
        if not link_host or link_host == host:
            internal_links_count += 1
        else:
            external_links_count += 1

    return ExtractedFeatures(
        title=title,
        meta_description=meta_description,
        h1=h1,
        h2_h3_json=h2_h3_json,
        main_text=main_text,
        has_faq=has_faq,
        has_tables=has_tables,
        has_lists=has_lists,
        has_cta=has_cta,
        has_forms=has_forms,
        has_prices=has_prices,
        has_reviews=has_reviews,
        has_contacts=has_contacts,
        internal_links_count=internal_links_count,
        external_links_count=external_links_count,
        has_schema_org=has_schema_org,
    )


def _text_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    clean = value.strip()
    return clean if clean else None
