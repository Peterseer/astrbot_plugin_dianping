from __future__ import annotations

import json
import re
from typing import Any
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


def first_content(node: Tag, selectors: tuple[str, ...]) -> str:
    for selector in selectors:
        found = node.select_one(selector)
        if found:
            value = clean_text(str(found.get("content") or found.get("data-address") or ""))
            if value:
                return value
    return ""


def _address_from_json(value: Any) -> str:
    if isinstance(value, dict):
        address = value.get("streetAddress") or value.get("shopAddress")
        if isinstance(address, str) and clean_text(address):
            return clean_text(address)
        nested = value.get("address")
        if isinstance(nested, str) and clean_text(nested):
            return clean_text(nested)
        if isinstance(nested, dict):
            found = _address_from_json(nested)
            if found:
                return found
        for child in value.values():
            found = _address_from_json(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _address_from_json(child)
            if found:
                return found
    return ""


def _extract_address(soup: BeautifulSoup, root: Tag, *, allow_global: bool = True) -> str:
    address = first_text(
        root,
        (
            "[itemprop=street-address]",
            "[itemprop=streetAddress]",
            "[itemprop=address]",
            ".tag-addr .addr",
            ".address .item",
            ".shop-address",
            ".address-info",
            ".shop-info .address",
            ".address",
            "[class*=address]",
            "[class*=shopAddr]",
        ),
    )
    if address:
        return re.sub(r"^(?:地址|商户地址)[：:]\s*", "", address).strip()

    address = first_content(
        root,
        (
            "meta[itemprop=streetAddress]",
            "meta[itemprop=street-address]",
            "meta[property='business:contact_data:street_address']",
            "meta[name=address]",
            "[data-address]",
        ),
    )
    if address:
        return address

    if not allow_global:
        return ""

    for script in soup.select("script[type='application/ld+json']"):
        try:
            payload = json.loads(script.string or script.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            continue
        address = _address_from_json(payload)
        if address:
            return address

    source = str(soup)
    for pattern in (
        r'\"(?:streetAddress|shopAddress|addressText)\"\s*:\s*\"((?:\\.|[^\"\\])+)\"',
        r"(?:地址|商户地址)\s*[：:]\s*([^\n\r<>]{3,160}?)(?=\s*(?:电话|营业时间|环境|交通|优惠|更多信息|$))",
    ):
        match = re.search(pattern, source, re.I)
        if not match:
            continue
        candidate = match.group(1)
        try:
            candidate = json.loads(f'"{candidate}"')
        except json.JSONDecodeError:
            pass
        candidate = clean_text(BeautifulSoup(candidate, "lxml").get_text(" ", strip=True))
        if candidate:
            return candidate

    body_text = clean_text(soup.get_text(" ", strip=True))
    match = re.search(
        r"(?:地址|商户地址)\s*[：:]\s*(.{3,160}?)(?=\s+(?:电话|营业时间|环境|交通|优惠|更多信息|查看商户|附近商户)(?:\s|[：:]))",
        body_text,
    )
    return clean_text(match.group(1)) if match else ""


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
                address=_extract_address(soup, node, allow_global=False),
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
    address = _extract_address(soup, root)
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
