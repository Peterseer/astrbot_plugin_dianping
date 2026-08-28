from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def load_city_ids() -> dict[str, int]:
    """Load the city table preserved from dianping_spider/docs/location.md."""
    path = Path(__file__).with_name("assets") / "location.md"
    text = path.read_text(encoding="utf-8")
    result: dict[str, int] = {}
    for name, raw_id in re.findall(r"^-\s*([^：:\r\n]+)[：:]\s*(\d+)\s*$", text, re.M):
        result[name.strip()] = int(raw_id)
    return result


def normalize_city_name(value: str) -> str:
    city = re.sub(r"\s+", "", value or "").strip()
    for suffix in ("特别行政区", "自治州", "地区", "盟", "市"):
        if city.endswith(suffix):
            city = city[: -len(suffix)]
            break
    aliases = {
        "北京市": "北京",
        "上海市": "上海",
        "天津市": "天津",
        "重庆市": "重庆",
        "香港特别行政区": "香港",
        "澳门特别行政区": "澳门",
    }
    return aliases.get(value.strip(), city)


def resolve_city_id(city: str, location: str, default_city_id: int) -> tuple[int, str]:
    mapping = load_city_ids()
    normalized = normalize_city_name(city)
    if normalized in mapping:
        return mapping[normalized], normalized

    haystack = f"{city} {location}"
    matches = [(len(name), name, city_id) for name, city_id in mapping.items() if name in haystack]
    if matches:
        _, name, city_id = max(matches)
        return city_id, name
    return default_city_id, ""

