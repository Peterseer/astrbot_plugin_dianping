import asyncio

from astrbot_plugin_dianping.client import DianpingClient
from astrbot_plugin_dianping.models import Dish, Restaurant


def test_ranking_combines_score_reviews_and_keyword_match():
    exact = Restaurant(
        shop_id="aaaaaaaaaaaaaaaa",
        name="潮汕牛肉火锅",
        score=4.6,
        review_count="500条评价",
        area="体育西",
        dishes=[Dish("吊龙")],
    )
    generic = Restaurant(
        shop_id="bbbbbbbbbbbbbbbb",
        name="普通火锅",
        score=4.7,
        review_count="600条评价",
        area="其他商圈",
    )
    ranked = DianpingClient._rank(
        [generic, exact], location="体育西", cuisine="潮汕牛肉火锅"
    )
    assert ranked[0] is exact


def test_restaurant_dict_hides_shop_id_by_default():
    item = Restaurant(shop_id="aaaaaaaaaaaaaaaa", name="测试餐厅")
    assert "shop_id" not in item.to_dict()
    assert item.to_dict(include_shop_id=True)["shop_id"] == "aaaaaaaaaaaaaaaa"


def test_enrichment_fills_address_for_every_result_even_with_zero_dish_limit():
    class StubClient(DianpingClient):
        async def get_album_detail(self, shop_id: str, *, fallback_name: str = "") -> Restaurant:
            return Restaurant(shop_id=shop_id, name=fallback_name, address=f"{fallback_name}地址")

    client = StubClient(cookie="test=1", user_agent="test-agent")
    items = [Restaurant(shop_id=f"shopid{i:010d}", name=f"餐厅{i}") for i in range(5)]
    asyncio.run(client.enrich_results(items, dish_limit=0))
    assert [item.address for item in items] == [f"餐厅{i}地址" for i in range(5)]
