from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from .models import Dish, Restaurant


_SPACE_RE = re.compile(r"\s+")
_PRICE_RE = re.compile(r"(?:¥|￥)\s*\d+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?\s*元(?:/[^\s]+)?")


def clean_text(value: str) -> str:
    return _SPACE_RE.sub(" ", value or "").strip()


def first_text(node: Tag, selectors: tuple[str, ...]) -> str:
    for selector in selectors:
        found = node.select_one(selector)
        if found:
            value = clean_text(found.get_text(" ", strip=True))
            if value:
                return value
    return ""


def _parse_score(node: Tag) -> float | None:
    score_text = first_text(node, (".star_score", ".score", "[class*=score]"))
    match = re.search(r"[0-5](?:\.\d)?", score_text)
    if match:
        return float(match.group())
    star = node.select_one(".star_icon span, span[class*=sml-str], span[class*=star]")
    if star:
        for class_name in star.get("class") or []:
            match = re.search(r"(?:star|str)[_-]?(\d{2})", class_name, re.I)
            if match:
                return int(match.group(1)) / 10
    return None


def _parse_shop_id(node: Tag, link: Tag | None) -> str:
    candidates = [
        node.get("data-shopid"),
        node.get("data-shop-id"),
        link.get("data-shopid") if link else None,
        link.get("data-shop-id") if link else None,
        link.get("href") if link else None,
    ]
    for value in candidates:
        if not value:
            continue
        match = re.search(r"(?:shop/|shopuuid=|shopId=)([A-Za-z0-9_-]{8,32})", str(value))
        if match:
            return match.group(1)
        if re.fullmatch(r"[A-Za-z0-9_-]{8,32}", str(value)):
            return str(value)
    return ""


def _parse_dishes(node: Tag) -> list[Dish]:
    dishes: list[Dish] = []
    seen: set[str] = set()
    containers = node.select(".recommend a, .recommend-name, .dish-name, [class*=dish-name]")
    for item in containers:
        name = clean_text(item.get_text(" ", strip=True))
        name = re.sub(r"^(?:推荐菜|招牌菜)[：:]?\s*", "", name)
        if not name or name in seen:
            continue
        parent_text = clean_text(item.parent.get_text(" ", strip=True)) if item.parent else ""
        price_match = _PRICE_RE.search(parent_text.replace(name, "", 1))
        dishes.append(Dish(name=name, price=price_match.group() if price_match else ""))
        seen.add(name)

    if not dishes:
        recommend = first_text(node, (".recommend", "[class*=recommend]"))
        recommend = re.sub(r"^(?:推荐菜|招牌菜)[：:]?\s*", "", recommend)
        for name in re.split(r"[、，,|/] |\s{2,}|[、，,|]", recommend):
            name = clean_text(name)
            if name and name not in seen and len(name) <= 40:
                dishes.append(Dish(name=name))
                seen.add(name)
    return dishes[:10]


def parse_search_page(html: str) -> list[Restaurant]:
    soup = BeautifulSoup(html, "lxml")
    nodes = soup.select(".shop-list > li, .shop-list li, li[data-shopid], [data-shop-id].shop-item")
    result: list[Restaurant] = []
    seen: set[str] = set()
    for index, node in enumerate(nodes):
        link = node.select_one(".tit a, .shop-name a, a[data-shopid], a[href*=shop]")
        name = first_text(node, (".tit a", ".shop-name", "[class*=shop-name]"))
        shop_id = _parse_shop_id(node, link)
        if not name or not shop_id or shop_id in seen:
            continue
        tags = [clean_text(item.get_text(" ", strip=True)) for item in node.select(".tag-addr .tag")]
        href = str(link.get("href") or "") if link else ""
        result.append(
            Restaurant(
                shop_id=shop_id,
                name=name,
                score=_parse_score(node),
                review_count=first_text(node, (".review-num", "[class*=review-num]", "[class*=review-count]")),
                avg_price=first_text(node, (".mean-price", "[class*=mean-price]", "[class*=avg-price]")),
                category=tags[0] if tags else "",
                area=tags[1] if len(tags) > 1 else "",
                address=first_text(node, (".tag-addr .addr", ".address", "[class*=address]")),
                dishes=_parse_dishes(node),
                detail_url=urljoin("https://www.dianping.com/", href),
                source_rank=index,
            )
        )
        seen.add(shop_id)
    return result


def parse_detail_page(html: str, shop_id: str, fallback_name: str = "") -> Restaurant:
    soup = BeautifulSoup(html, "lxml")
    root = soup.select_one(".main") or soup
    name = first_text(root, ("#basic-info .shop-name", "h1.shop-name", "h1")) or fallback_name
    phone = first_text(root, ("#basic-info .tel", ".tel", "[itemprop=telephone]"))
    address = first_text(root, ("[itemprop=street-address]", "[itemprop=address]", ".address"))
    return Restaurant(
        shop_id=shop_id,
        name=name,
        score=_parse_score(root),
        review_count=first_text(root, ("#reviewCount", ".review-count")),
        avg_price=first_text(root, ("#avgPriceTitle", ".avg-price")),
        address=address,
        phone=phone,
        dishes=_parse_dishes(root),
        detail_url=f"https://www.dianping.com/shop/{shop_id}",
    )

