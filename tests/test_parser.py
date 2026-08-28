from astrbot_plugin_dianping.parser import parse_detail_page, parse_search_page


SEARCH_HTML = """
<html><body><div class="shop-list"><ul>
  <li>
    <div class="txt">
      <div class="tit"><a data-shopid="k30YbaScPKFS0hfP" href="/shop/k30YbaScPKFS0hfP">潮牛馆</a></div>
      <div class="comment"><span class="star_score">4.7</span><a class="review-num">1234条评价</a>
        <a class="mean-price"><b>￥128</b></a></div>
      <div class="tag-addr"><span class="tag">潮汕牛肉火锅</span><span class="tag">体育西</span>
        <span class="addr">天河路100号</span></div>
      <div class="recommend">推荐菜：<a>吊龙</a><a>五花趾</a></div>
    </div>
  </li>
</ul></div></body></html>
"""


DETAIL_HTML = """
<html><body><div class="main"><div id="basic-info">
  <h1 class="shop-name">潮牛馆</h1>
  <div class="brief-info"><span id="reviewCount">1234条评论</span><span id="avgPriceTitle">人均￥128</span></div>
  <span itemprop="street-address">天河路100号</span><p class="tel">020-12345678</p>
  <div class="recommend"><span class="dish-name">吊龙</span><span class="dish-price">￥48</span></div>
</div></div></body></html>
"""


def test_parse_search_page_fields():
    items = parse_search_page(SEARCH_HTML)
    assert len(items) == 1
    item = items[0]
    assert item.shop_id == "k30YbaScPKFS0hfP"
    assert item.name == "潮牛馆"
    assert item.score == 4.7
    assert item.avg_price == "￥128"
    assert item.address == "天河路100号"
    assert [dish.name for dish in item.dishes] == ["吊龙", "五花趾"]
    assert all(not dish.price for dish in item.dishes)


def test_parse_detail_page_phone_address_and_dish_price():
    item = parse_detail_page(DETAIL_HTML, "k30YbaScPKFS0hfP")
    assert item.phone == "020-12345678"
    assert item.address == "天河路100号"
    assert item.dishes[0].name == "吊龙"
    assert item.dishes[0].price == "￥48"

