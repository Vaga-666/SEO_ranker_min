import re


def parse_keywords(raw: str) -> list[str]:
    if not raw:
        return []

    parts = re.split(r"[\n,]+", raw)
    result: list[str] = []
    seen: set[str] = set()

    for part in parts:
        keyword = part.strip()
        if not keyword or keyword in seen:
            continue
        seen.add(keyword)
        result.append(keyword)

    return result
