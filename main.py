from __future__ import annotations

import json
import re
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .client import DianpingClient
from .errors import DianpingError
from .models import Restaurant


PLUGIN_NAME = "astrbot_plugin_dianping"


@register(
    PLUGIN_NAME,
    "Peterseer",
    "通过大众点评搜索附近餐厅，并基于评分、口味、招牌菜和价格提供推荐",
    "1.0.1",
    "https://github.com/Peterseer/astrbot_plugin_dianping",
)
class DianpingPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config: dict[str, Any] = config or {}
        self.client = DianpingClient(
            cookie=str(self.config.get("cookie", "") or ""),
            user_agent=str(
                self.config.get(
                    "user_agent",
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
                )
            ),
            timeout_seconds=self._int_config("request_timeout_seconds", 20),
            request_interval_seconds=self._float_config("request_interval_seconds", 2.5),
            max_retries=self._int_config("max_retries", 1),
            proxy_url=str(self.config.get("proxy_url", "") or ""),
            default_city_id=self._int_config("default_city_id", 1),
            search_pages=self._int_config("search_pages", 1),
            cache_ttl_seconds=self._int_config("cache_ttl_seconds", 600),
            uuid=str(self.config.get("uuid", "") or ""),
            tcv=str(self.config.get("tcv", "") or ""),
        )
        self._recent: dict[str, list[Restaurant]] = {}

    def _int_config(self, name: str, default: int) -> int:
        try:
            return int(self.config.get(name, default))
        except (TypeError, ValueError):
            return default

    def _float_config(self, name: str, default: float) -> float:
        try:
            return float(self.config.get(name, default))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _session_key(event: AstrMessageEvent) -> str:
        return str(event.unified_msg_origin or event.get_sender_id())

    def _remember(self, event: AstrMessageEvent, items: list[Restaurant]) -> None:
        self._recent[self._session_key(event)] = items

    def _max_results(self, requested: int | float) -> int:
        configured = max(1, min(self._int_config("max_results", 5), 10))
        try:
            value = int(requested)
        except (TypeError, ValueError):
            value = configured
        return max(1, min(value, configured))

    async def _search(
        self,
        event: AstrMessageEvent,
        *,
        location: str,
        cuisine: str,
        city: str,
        max_results: int | float,
    ) -> tuple[list[Restaurant], str]:
        items, resolved_city = await self.client.search(
            location=location,
            cuisine=cuisine,
            city=city,
            max_results=self._max_results(max_results),
        )
        detail_limit = max(0, min(self._int_config("detail_fetch_limit", 3), len(items)))
        await self.client.enrich_results(items, dish_limit=detail_limit)
        self._remember(event, items)
        return items, resolved_city

    @staticmethod
    def _dish_text(item: Restaurant) -> str:
        if not item.dishes:
            return "页面未列出"
        values = [f"{dish.name}（{dish.price}）" if dish.price else dish.name for dish in item.dishes]
        return "、".join(values)

    @classmethod
    def _format_items(cls, items: list[Restaurant]) -> str:
        if not items:
            return "没有找到符合条件的餐厅。"
        lines: list[str] = []
        for index, item in enumerate(items, 1):
            score = f"{item.score:.1f}/5" if item.score is not None else "页面未显示"
            lines.extend(
                [
                    f"{index}. {item.name}",
                    f"   评分: {score}；评论数: {item.review_count or '页面未显示'}；人均: {item.avg_price or '页面未显示'}",
                    f"   类型/商圈: {' / '.join(value for value in (item.category, item.area) if value) or '页面未显示'}",
                    f"   地址: {item.address or '页面未显示'}",
                    f"   招牌/推荐菜: {cls._dish_text(item)}",
                    f"   大众点评: {item.detail_url or f'https://www.dianping.com/shop/{item.shop_id}'}",
                ]
            )
        return "\n".join(lines)

    @classmethod
    def _grounding_prompt(cls, items: list[Restaurant], *, location: str, cuisine: str, city: str) -> str:
        return (
            "以下是刚从大众点评页面取得并按评分、评论量、地点/口味匹配度重排的候选餐厅。"
            "请用当前 Persona 的自然中文回答用户，推荐 3-5 家并简要说明匹配理由。"
            "只能使用资料中的事实；不得补写菜价、地址、评分或电话。菜品后括号内才是该菜的独立标价；"
            "“人均”不是菜价。资料缺失时明确说页面未显示。默认不要提供电话；如果用户需要，"
            "请再调用 get_dianping_restaurant_phone，并传入完整店名 restaurant_name。"
            "默认回答不得显示或索要内部 shop_id。保留可点击的大众点评链接。\n"
            f"查询城市: {city or '使用插件默认城市 ID'}；地点: {location}；想吃: {cuisine or '不限'}\n\n"
            f"<大众点评餐厅资料>\n{cls._format_items(items)}\n</大众点评餐厅资料>"
        )

    def _resolve_recent(self, event: AstrMessageEvent, shop_id: str, restaurant_name: str) -> tuple[str, str]:
        shop_id = shop_id.strip()
        restaurant_name = restaurant_name.strip()
        if shop_id:
            recent = self._recent.get(self._session_key(event), [])
            name = next((item.name for item in recent if item.shop_id == shop_id), restaurant_name)
            return shop_id, name
        if not restaurant_name:
            return "", ""
        recent = self._recent.get(self._session_key(event), [])
        exact = [item for item in recent if item.name == restaurant_name]
        matches = exact or [item for item in recent if restaurant_name in item.name or item.name in restaurant_name]
        if len(matches) == 1:
            return matches[0].shop_id, matches[0].name
        return "", restaurant_name

    @filter.llm_tool(name="search_dianping_restaurants")
    async def search_dianping_restaurants(
        self,
        event: AstrMessageEvent,
        location: str,
        cuisine: str = "",
        city: str = "",
        max_results: float = 5,
    ) -> str:
        """在用户指定地点附近搜索大众点评餐厅，结合想吃的口味、评分和评论量返回推荐依据。用户询问附近餐厅、哪里吃饭、某商圈餐厅或某类食物推荐时使用。不要用它查询非餐饮商户或店铺电话。

        Args:
            location(string): 具体地点、商圈、地标、街道或区域，必填；尽量保留用户原话
            cuisine(string): 想吃的菜系、菜品、口味或用餐需求；未指定时留空
            city(string): 地点所在城市；能从用户话中识别时填写，否则留空使用插件默认城市
            max_results(number): 候选餐厅数量，1-10；通常使用5
        """
        try:
            items, resolved_city = await self._search(
                event,
                location=location,
                cuisine=cuisine,
                city=city,
                max_results=max_results,
            )
        except DianpingError as exc:
            logger.warning("[Dianping] 餐厅搜索失败: %s", exc)
            return f"大众点评查询失败：{exc} 请如实告知用户，不要凭空推荐餐厅。"
        return self._grounding_prompt(
            items,
            location=location,
            cuisine=cuisine,
            city=resolved_city or city,
        )

    @filter.llm_tool(name="get_dianping_restaurant_phone")
    async def get_dianping_restaurant_phone(
        self,
        event: AstrMessageEvent,
        shop_id: str = "",
        restaurant_name: str = "",
    ) -> str:
        """按需查询刚才推荐的大众点评餐厅公开联系电话。只有用户明确索要电话、联系方式或想致电店铺时使用；不要在普通推荐中主动调用。

        Args:
            shop_id(string): 内部店铺 ID；通常留空，仅兼容其他集成直接传入
            restaurant_name(string): 用户所指的完整店名；用于匹配本会话最近推荐，优先填写
        """
        resolved_id, resolved_name = self._resolve_recent(event, shop_id, restaurant_name)
        if not resolved_id:
            recent = self._recent.get(self._session_key(event), [])
            choices = "、".join(item.name for item in recent)
            if choices:
                return f"无法唯一确定用户所指店铺。请先让用户确认：{choices}。不要猜测电话。"
            return "本会话没有可匹配的最近推荐记录。请先搜索餐厅，不要猜测电话。"
        try:
            detail = await self.client.get_detail(
                resolved_id,
                fallback_name=resolved_name,
                include_phone=True,
            )
        except DianpingError as exc:
            logger.warning("[Dianping] 电话查询失败: %s", exc)
            return f"店铺电话查询失败：{exc} 请如实说明，不要编造号码。"
        if not detail.phone or detail.phone in {"-", "暂无"}:
            return (
                f"大众点评当前没有显示 {detail.name or resolved_name or resolved_id} 的可用电话。"
                "请直接这样告知用户，不要生成或猜测号码。"
            )
        payload = {
            "restaurant": detail.name or resolved_name,
            "phone": detail.phone,
            "address": detail.address or "页面未显示",
            "source": detail.detail_url,
        }
        return (
            "这是大众点评页面当前展示的商户公开联系电话。请按当前 Persona 简洁转述，"
            "号码必须逐字保留，并附来源链接；不要添加资料外的号码。\n"
            + json.dumps(payload, ensure_ascii=False)
        )

    @filter.command("点评推荐", alias={"附近餐厅", "餐厅推荐"})
    async def dianping_command(self, event: AstrMessageEvent):
        """搜索餐厅。格式：/点评推荐 地点 | 想吃什么"""
        text = str(getattr(event, "message_str", "") or "")
        body = re.sub(r"^/?(?:点评推荐|附近餐厅|餐厅推荐)\s*", "", text, count=1).strip()
        if not body:
            yield event.plain_result("用法：/点评推荐 地点 | 想吃什么\n示例：/点评推荐 广州天河体育中心 | 粤菜")
            return
        location, separator, cuisine = body.partition("|")
        if not separator:
            location, separator, cuisine = body.partition("｜")
        try:
            items, _ = await self._search(
                event,
                location=location.strip(),
                cuisine=cuisine.strip(),
                city="",
                max_results=self._int_config("max_results", 5),
            )
        except DianpingError as exc:
            yield event.plain_result(f"大众点评查询失败：{exc}")
            return
        yield event.plain_result(self._format_items(items))

    @filter.command("点评电话")
    async def dianping_phone_command(self, event: AstrMessageEvent):
        """查询最近推荐店铺的电话。格式：/点评电话 完整店名"""
        text = str(getattr(event, "message_str", "") or "")
        body = re.sub(r"^/?点评电话\s*", "", text, count=1).strip()
        if not body:
            yield event.plain_result("用法：/点评电话 推荐结果中的完整店名")
            return
        is_id = bool(re.fullmatch(r"[A-Za-z0-9_-]{8,32}", body))
        resolved_id, resolved_name = self._resolve_recent(event, body if is_id else "", "" if is_id else body)
        if not resolved_id:
            yield event.plain_result("无法唯一确定店铺，请输入推荐结果中的完整店名再试。")
            return
        try:
            detail = await self.client.get_detail(resolved_id, fallback_name=resolved_name, include_phone=True)
        except DianpingError as exc:
            yield event.plain_result(f"店铺电话查询失败：{exc}")
            return
        if detail.phone and detail.phone not in {"-", "暂无"}:
            yield event.plain_result(
                f"{detail.name or resolved_name}\n电话：{detail.phone}\n"
                f"地址：{detail.address or '页面未显示'}\n来源：{detail.detail_url}"
            )
        else:
            yield event.plain_result(f"大众点评当前没有显示 {detail.name or resolved_name} 的可用电话。")

    @filter.command("点评状态")
    async def dianping_status_command(self, event: AstrMessageEvent):
        status = "已填写 Cookie，可以发起查询" if self.client.is_configured else "未填写 Cookie，暂不能查询"
        phone_mode = "已配置 uuid/tcv，可尝试查询完整电话" if self.client.uuid and self.client.tcv else "未配置 uuid/tcv，仅解析详情页可见电话"
        yield event.plain_result(f"大众点评插件：{status}\n电话模式：{phone_mode}")

    async def terminate(self):
        await self.client.close()
