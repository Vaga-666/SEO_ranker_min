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
    has_lead_form: bool
    has_prices: bool
    has_courses: bool
    has_reviews: bool
    has_contacts: bool
    internal_links_count: int
    external_links_count: int
    has_schema_org: bool


CTA_MARKERS = [
    "купить",
    "заказать",
    "оставить заявку",
    "подписаться",
    "получить",
    "связаться",
    "начать бесплатно",
    "начать обучение",
    "смотреть курсы",
    "выбрать курс",
    "отправить заявку",
    "задать вопрос",
    "как проходит обучение",
    "#courses",
    "mailto:",
]

FORM_MARKERS = [
    "<form",
    "input",
    "textarea",
    "name",
    "contact",
    "message",
    "ваше имя",
    "email или telegram",
    "нужна помощь с выбором курса",
    "отправить заявку",
]

FAQ_MARKERS = [
    "faq",
    "вопрос-ответ",
    "можно ли",
    "чем отличается",
    "что такое",
    "часто задаваем",
]

REVIEW_MARKERS = [
    "отзыв",
    "отзывы",
    "отзывы и результаты",
    "reviews",
    "ученик",
    "студент",
    "пользователь",
    "преподаватель",
    "кейсы",
    "результаты",
]

CONTACT_MARKERS = [
    "контакт",
    "контакты",
    "support@bots-ai.net",
    "support@",
    "mailto:",
    "email",
    "e-mail",
    "сайт",
    "формат работы",
    "telegram",
    "whatsapp",
]

COMMERCIAL_MARKERS = [
    "бесплатно",
    "бесплатные курсы",
    "купить",
    "цена",
    "стоимость",
    "платные",
    "платный",
    "платные практические курсы",
    "форматы обучения",
    "записаться",
    "начать бесплатно",
]

COURSE_MARKERS = [
    "курсы",
    "доступные курсы",
    "курс",
    "python",
    "sql",
    "обучение",
    "форматы обучения",
]


def extract_page_features(html: str, page_url: str) -> ExtractedFeatures:
    soup = BeautifulSoup(html or "", "lxml")
    host = urlparse(page_url).netloc.lower().replace("www.", "")

    title = _text_or_none(soup.title.string if soup.title else None)
    meta_description_tag = soup.find("meta", attrs={"name": "description"})
    meta_description = _text_or_none(meta_description_tag.get("content") if meta_description_tag else None)

    h1_tag = soup.find("h1")
    h1 = _text_or_none(h1_tag.get_text(" ", strip=True) if h1_tag else None)

    h2_h3 = [h.get_text(" ", strip=True) for h in soup.select("h2, h3")]
    h2_h3_json = json.dumps([t for t in h2_h3 if t], ensure_ascii=False)

    ld_json_types = _extract_ld_json_types(soup)
    raw_html_lower = (html or "").lower()

    for bad in soup(["script", "style", "noscript"]):
        bad.decompose()
    text_chunks = [t.strip() for t in soup.stripped_strings]
    main_text = _text_or_none(" ".join(text_chunks[:1500]))

    page_text = (main_text or "").lower()
    title_text = (title or "").lower()
    heading_text = " ".join([h1 or "", *h2_h3]).lower()
    link_text = " ".join([a.get_text(" ", strip=True) + " " + str(a.get("href") or "") for a in soup.find_all("a")]).lower()
    button_text = " ".join([b.get_text(" ", strip=True) for b in soup.find_all("button")]).lower()
    form_text = " ".join([f.get_text(" ", strip=True) for f in soup.find_all("form")]).lower()
    input_values = " ".join(
        [
            " ".join(
                [
                    str(tag.get("name") or ""),
                    str(tag.get("placeholder") or ""),
                    str(tag.get("aria-label") or ""),
                    str(tag.get("value") or ""),
                    str(tag.get("type") or ""),
                ]
            )
            for tag in soup.find_all(["input", "textarea", "button"])
        ]
    ).lower()
    all_search_text = " ".join(
        [title_text, heading_text, page_text, link_text, button_text, form_text, input_values, raw_html_lower]
    )

    has_faq = (
        "FAQPage" in ld_json_types
        or _contains_any(all_search_text, FAQ_MARKERS)
        or all_search_text.count("?") >= 3
    )
    has_tables = bool(soup.find("table"))
    has_lists = bool(soup.find("ul") or soup.find("ol"))
    has_form_tag = bool(soup.find("form"))
    has_inputs = bool(soup.find("input"))
    has_textareas = bool(soup.find("textarea"))
    has_mailto = "mailto:" in raw_html_lower
    has_forms = has_form_tag or (has_inputs and has_textareas) or has_mailto
    has_lead_form = has_forms and _contains_any(all_search_text, FORM_MARKERS + ["заявка", "курс", "вопрос"])
    has_schema_org = bool(soup.find(attrs={"itemtype": re.compile(r"schema\.org", re.IGNORECASE)})) or bool(ld_json_types)
    has_cta = _contains_any(all_search_text, CTA_MARKERS)
    has_prices = bool(re.search(r"\b\d[\d\s]*(?:₽|руб|rub|\$|€|eur|usd)\b", all_search_text, re.IGNORECASE)) or _contains_any(
        all_search_text, COMMERCIAL_MARKERS
    )
    has_courses = _contains_any(all_search_text, COURSE_MARKERS)
    has_reviews = _contains_any(all_search_text, REVIEW_MARKERS)
    has_contacts = _contains_any(all_search_text, CONTACT_MARKERS)

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
        has_lead_form=has_lead_form,
        has_prices=has_prices,
        has_courses=has_courses,
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


def _contains_any(haystack: str, needles: list[str]) -> bool:
    return any(needle.lower() in haystack for needle in needles)


def _extract_ld_json_types(soup: BeautifulSoup) -> set[str]:
    types: set[str] = set()
    for script in soup.find_all("script", attrs={"type": re.compile(r"application/ld\+json", re.IGNORECASE)}):
        raw = script.string or script.get_text() or ""
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except Exception:
            for known in ["Organization", "WebSite", "WebPage", "FAQPage", "BreadcrumbList", "Course", "ItemList"]:
                if known.lower() in raw.lower():
                    types.add(known)
            continue
        _collect_schema_types(data, types)
    return types


def _collect_schema_types(value: object, out: set[str]) -> None:
    if isinstance(value, dict):
        type_value = value.get("@type")
        if isinstance(type_value, str):
            out.add(type_value)
        elif isinstance(type_value, list):
            for item in type_value:
                if isinstance(item, str):
                    out.add(item)
        graph = value.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                _collect_schema_types(item, out)
        for nested in value.values():
            if isinstance(nested, (dict, list)):
                _collect_schema_types(nested, out)
    elif isinstance(value, list):
        for item in value:
            _collect_schema_types(item, out)
