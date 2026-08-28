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

