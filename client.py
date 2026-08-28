from __future__ import annotations

import asyncio
import base64
import json
import math
import re
import time
import zlib
from typing import Any
from urllib.parse import quote

import aiohttp
from bs4 import BeautifulSoup

from .city_ids import resolve_city_id
from .errors import DianpingBlockedError, DianpingConfigError, DianpingParseError
from .font_decoder import DianpingFontDecoder
from .models import Dish, Restaurant
from .parser import clean_text, parse_detail_page, parse_search_page


class DianpingClient:
    VERIFY_MARKERS = ("验证中心", "访问验证", "verify.meituan.com", "请拖动滑块")

    def __init__(
        self,
        *,
        cookie: str,
        user_agent: str,
        timeout_seconds: int = 20,
        request_interval_seconds: float = 2.0,
        max_retries: int = 1,
        proxy_url: str = "",
        default_city_id: int = 1,
        search_pages: int = 1,
        cache_ttl_seconds: int = 600,
        uuid: str = "",
        tcv: str = "",
    ) -> None:
        self.cookie = clean_text(cookie)
        self.user_agent = user_agent.strip()
        self.timeout_seconds = max(5, min(int(timeout_seconds), 60))
        self.request_interval_seconds = max(0.5, float(request_interval_seconds))
        self.max_retries = max(0, min(int(max_retries), 3))
        self.proxy_url = proxy_url.strip() or None
        self.default_city_id = max(1, int(default_city_id))
        self.search_pages = max(1, min(int(search_pages), 3))
        self.cache_ttl_seconds = max(0, int(cache_ttl_seconds))
        self.uuid = uuid.strip()
        self.tcv = tcv.strip()
        self.decoder = DianpingFontDecoder()
        self._session: aiohttp.ClientSession | None = None
        self._request_lock = asyncio.Lock()
        self._last_request_at = 0.0
        self._cache: dict[str, tuple[float, list[Restaurant]]] = {}

    @property
    def is_configured(self) -> bool:
        return bool(self.cookie and self.user_agent)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def _request(self, url: str, *, referer: str = "https://www.dianping.com/") -> bytes:
        if not self.is_configured:
            raise DianpingConfigError("请先在插件配置中填写有效的大众点评 Cookie 和浏览器 User-Agent。")
        headers = {
            "User-Agent": self.user_agent,
            "Cookie": self.cookie,
            "Referer": referer,
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        }
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                async with self._request_lock:
                    wait_for = self.request_interval_seconds - (time.monotonic() - self._last_request_at)
                    if wait_for > 0:
                        await asyncio.sleep(wait_for)
                    session = await self._get_session()
                    async with session.get(
                        url,
                        headers=headers,
                        proxy=self.proxy_url,
                        allow_redirects=True,
                    ) as response:
                        body = await response.read()
                        self._last_request_at = time.monotonic()
                        text = body.decode("utf-8", errors="ignore")
                        if response.status in (403, 406, 418, 429) or any(
                            marker in text or marker in str(response.url) for marker in self.VERIFY_MARKERS
                        ):
                            raise DianpingBlockedError(
                                "大众点评触发了访问验证。请在浏览器完成验证后更新 Cookie，"
                                "并适当增大请求间隔；不要让 Bot 高频重试。"
                            )
                        if response.status >= 400:
                            raise DianpingParseError(f"大众点评返回 HTTP {response.status}。")
                        return body
            except DianpingBlockedError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError, DianpingParseError) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    await asyncio.sleep(1.0 + attempt)
        raise DianpingParseError(f"访问大众点评失败：{last_error}")

    async def _get_text(self, url: str, *, referer: str = "https://www.dianping.com/") -> str:
        return (await self._request(url, referer=referer)).decode("utf-8", errors="ignore")

    async def search(
        self,
        *,
        location: str,
        cuisine: str,
        city: str = "",
        max_results: int = 5,
    ) -> tuple[list[Restaurant], str]:
        location = clean_text(location)
        cuisine = clean_text(cuisine)
        if not location:
            raise DianpingConfigError("缺少地点。请提供商圈、地标、街道或区域。")
        city_id, resolved_city = resolve_city_id(city, location, self.default_city_id)
        keyword = clean_text(f"{location} {cuisine}")
        cache_key = f"{city_id}|{keyword}|{max_results}"
        cached = self._cache.get(cache_key)
        if cached and time.monotonic() - cached[0] <= self.cache_ttl_seconds:
            return [self._clone(item) for item in cached[1]], resolved_city

        restaurants: list[Restaurant] = []
        for page in range(1, self.search_pages + 1):
            base = f"https://www.dianping.com/search/keyword/{city_id}/10_{quote(keyword, safe='')}"
            url = base if page == 1 else f"{base}/p{page}"
            raw_html = await self._get_text(url)
            html = await self.decoder.decode_html(raw_html, self._request)
            page_items = parse_search_page(html)
            if not page_items:
                if "not-found-right" in html or "没有找到" in html:
                    break
                raise DianpingParseError(
                    "搜索页没有解析出店铺。Cookie 可能已失效，或大众点评页面结构已经变化。"
                )
            restaurants.extend(page_items)
            if len(page_items) < 15:
                break

        ranked = self._rank(restaurants, location=location, cuisine=cuisine)
        limit = max(1, min(int(max_results), 10))
        selected = ranked[:limit]
        self._cache[cache_key] = (time.monotonic(), [self._clone(item) for item in selected])
        return selected, resolved_city

    async def get_detail(self, shop_id: str, *, fallback_name: str = "", include_phone: bool = False) -> Restaurant:
        shop_id = clean_text(shop_id)
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,32}", shop_id):
            raise DianpingConfigError("店铺 ID 格式不正确。请使用推荐结果中的 shop_id。")
        url = f"https://www.dianping.com/shopold/pc?shopuuid={quote(shop_id)}"
        raw_html = await self._get_text(url)
        html = await self.decoder.decode_html(raw_html, self._request)
        detail = parse_detail_page(html, shop_id, fallback_name)
        if include_phone and self.uuid and self.tcv:
            api_phone = await self._get_phone_from_api(shop_id, url)
            if api_phone:
                detail.phone = api_phone
        return detail

    async def enrich_dishes(self, items: list[Restaurant], *, limit: int = 3) -> None:
        """Best-effort detail fetch for missing dish names/prices; failures keep search data."""
        for item in items[: max(0, limit)]:
            if item.dishes and any(dish.price for dish in item.dishes):
                continue
            try:
                detail = await self.get_detail(item.shop_id, fallback_name=item.name)
            except Exception:
                continue
            if detail.address and not item.address:
                item.address = detail.address
            if detail.dishes:
                item.dishes = self._merge_dishes(item.dishes, detail.dishes)

    async def _get_phone_from_api(self, shop_id: str, origin_url: str) -> str:
        token = self._token(origin_url)
        params = {
            "shopId": shop_id,
            "_token": token,
            "tcv": self.tcv,
            "uuid": self.uuid,
            "platform": "1",
            "partner": "150",
            "optimusCode": "10",
            "originUrl": origin_url,
        }
        query = "&".join(f"{quote(str(key))}={quote(str(value), safe='')}" for key, value in params.items())
        url = f"https://www.dianping.com/ajax/json/shopDynamic/basicHideInfo?{query}"
        try:
            payload: dict[str, Any] = json.loads((await self._request(url, referer=origin_url)).decode("utf-8"))
        except (json.JSONDecodeError, DianpingParseError):
            return ""
        if payload.get("code") != 200:
            return ""
        info = (payload.get("msg") or {}).get("shopInfo") or {}
        values = [self.decoder.decode_fragment(str(info.get(key) or "")) for key in ("phoneNo", "phoneNo2")]
        return "、".join(value for value in values if value)

    @staticmethod
    def _token(shop_url: str) -> str:
        now = int(time.time() * 1000)
        payload = str(
            {
                "rId": "100041",
                "ver": "1.0.6",
                "ts": now,
                "cts": now - 600,
                "brVD": [1920, 186],
                "brR": [[1920, 1080], [1920, 1040], 24, 24],
                "bI": [shop_url, shop_url],
                "mT": ["1244,588"],
                "kT": [],
                "aT": [],
                "tT": [],
                "aM": "",
                "sign": "eJxTKs7IL/BMsTU2NTAwMLVUAgApvgRP",
            }
        ).encode()
        return base64.b64encode(zlib.compress(payload)).decode()

    @staticmethod
    def _rank(items: list[Restaurant], *, location: str, cuisine: str) -> list[Restaurant]:
        tokens = [token.lower() for token in re.split(r"[\s,，、/]+", f"{location} {cuisine}") if token]
        for item in items:
            haystack = " ".join(
                [item.name, item.category, item.area, item.address, " ".join(dish.name for dish in item.dishes)]
            ).lower()
            relevance = sum(1 for token in tokens if token in haystack)
            score = item.score if item.score is not None else 3.0
            review_number_match = re.search(r"(\d+(?:\.\d+)?)\s*万", item.review_count)
            if review_number_match:
                reviews = float(review_number_match.group(1)) * 10000
            else:
                plain_match = re.search(r"\d+", item.review_count.replace(",", ""))
                reviews = float(plain_match.group()) if plain_match else 0.0
            confidence = min(math.log10(reviews + 1), 4.0)
            item.rank_score = score * 10 + confidence * 1.5 + relevance * 3 - item.source_rank * 0.03
        return sorted(items, key=lambda item: item.rank_score, reverse=True)

    @staticmethod
    def _merge_dishes(first: list[Dish], second: list[Dish]) -> list[Dish]:
        merged: dict[str, Dish] = {dish.name: Dish(dish.name, dish.price) for dish in first}
        for dish in second:
            if dish.name not in merged or (dish.price and not merged[dish.name].price):
                merged[dish.name] = Dish(dish.name, dish.price)
        return list(merged.values())[:10]

    @staticmethod
    def _clone(item: Restaurant) -> Restaurant:
        return Restaurant(
            shop_id=item.shop_id,
            name=item.name,
            score=item.score,
            review_count=item.review_count,
            avg_price=item.avg_price,
            category=item.category,
            area=item.area,
            address=item.address,
            dishes=[Dish(dish.name, dish.price) for dish in item.dishes],
            detail_url=item.detail_url,
            phone=item.phone,
            sub_scores=dict(item.sub_scores),
            source_rank=item.source_rank,
            rank_score=item.rank_score,
        )

