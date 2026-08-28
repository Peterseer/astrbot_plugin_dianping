from astrbot_plugin_dianping.city_ids import load_city_ids, resolve_city_id


def test_city_table_and_explicit_city_resolution():
    cities = load_city_ids()
    assert cities["上海"] == 1
    assert cities["香港"] == 341
    assert resolve_city_id("广州市", "天河体育中心", 1) == (4, "广州")


def test_city_can_be_inferred_from_location():
    assert resolve_city_id("", "深圳南山区科技园", 1) == (7, "深圳")
    assert resolve_city_id("", "未知地点", 8) == (8, "")

