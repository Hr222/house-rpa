# -*- coding: utf-8 -*-
"""房天下核心挂牌与成交 HTML 解析测试。"""

from app.rpa.platforms.fang.parser import (
    parse_deal_records,
    parse_listing_snapshots,
)


def test_parse_listing_and_deal_records_for_storage():
    """正常房源：小区名(add_shop)/标题(tit_shop)/户型/面积/总价/单价。"""
    html = """
    <dl class="clearfix">
      <dd><h4><a><span class="tit_shop">绿景虹湾 精装三房</span></a></h4>
          <p class="tel_shop">3室2厅 | 88.35㎡ | 南北</p>
          <p class="add_shop"><a href="//sz.fang.com/house-xm2810892392/" title="绿景虹湾"> 绿景虹湾 </a><span>梅林 北环大道6098号</span></p></dd>
      <dd class="price_right"><span class="red"><b>530</b>万</span><span>59988元/㎡</span></dd>
    </dl>
    """
    snapshots = parse_listing_snapshots(html)

    assert len(snapshots) == 1
    s = snapshots[0]
    assert s.community_name == "绿景虹湾"  # add_shop a 提取
    assert s.title == "绿景虹湾 精装三房"     # tit_shop 提取
    assert s.layout == "3室2厅"
    assert s.area == 88.35
    assert s.total_price == 530.0
    assert s.unit_price == 59988.0

    deal_html = """
    <table class="table_hx"><tbody>
      <tr><th>房源面积</th><th>成交时间</th><th>成交总价</th><th>成交均价</th></tr>
      <tr><td><p>75.14㎡</p></td><td><p>2026-05-06</p></td><td><p>558万</p></td><td><p>74262元/㎡</p></td></tr>
    </tbody></table>
    """
    records = parse_deal_records(deal_html)

    assert len(records) == 1
    area, date_str, total, price = records[0]
    assert area == 75.14
    assert date_str == "2026-05-06"
    assert total == 558
    assert price == 74262.0


# ============================================================
# 小区入口初始化脚本（匿名联想 API 版）的载荷解析契约
#
# 背景：2026-09-30 实测房天下风控是**路径级**——/house/kw 搜索页、
# /house-xm 挂牌页、/loupan 成交页均返回滑块验证码页，而联想接口
# /asynclist/searchsuggestion/suggestionList 匿名可用。旧浏览器版把该接口
# 当兜底却用 json.loads 解析 `fangcb({'hit':...})`（JS 对象字面量，key 单引号），
# 431 次调用 0 次命中——兜底从未生效。以下用例锁定该回归。
# ============================================================


def test_parse_suggest_payload_accepts_js_object_literal():
    """响应是 JS 对象字面量（单引号 key），必须能解析（旧实现 json.loads 必然抛错）。"""
    from scripts.initialize_community_page.community_page.fang_community_page_mvp import (
        parse_suggest_payload,
    )

    raw = (
        "fangcb({'hit':[{\"ywtype\":\"出售\",\"wordtype\":\"楼盘\",\"id\":\"2810258062\","
        "\"projname\":\"泰华花园\",\"district\":\"宝安\",\"comerce\":\"新安\","
        "\"projaliasnames\":\"泰华花园 泰华\",\"esfcount\":\"0\"},"
        "{\"wordtype\":\"小区\",\"id\":\"999\"}],\"history\":[]})"
    )
    entries = parse_suggest_payload(raw)

    assert len(entries) == 1  # wordtype != 楼盘 的条目被丢弃
    entry = entries[0]
    assert entry.community_id == "2810258062"
    assert entry.projname == "泰华花园"
    assert entry.district == "宝安"
    assert entry.area == "新安"
    assert entry.aliases == ("泰华花园", "泰华")
    assert entry.on_sale_count == 0  # 计数是字符串 "0"


def test_parse_suggest_payload_empty_hit_is_valid_not_failure():
    """合法空结果（hit 为空数组）是“查无此名”，不是接口失效。"""
    from scripts.initialize_community_page.community_page.fang_community_page_mvp import (
        parse_suggest_payload,
    )

    assert parse_suggest_payload("fangcb({'hit':[],\"history\":[]})") == []


def test_parse_suggest_payload_raises_on_slider_captcha():
    """滑块验证码页必须判定为契约失效并抛错，绝不能静默返回空列表。

    静默返回空会把拦截图成“小区不存在”——2026-09-29 宝安 461 条假失败即源于此。
    """
    from scripts.initialize_community_page.community_page.fang_community_page_mvp import (
        FangSuggestBlockedError,
        parse_suggest_payload,
    )
    import pytest

    captcha = (
        '<!DOCTYPE html><html><head><meta charset="utf-8"><title></title>'
        '<link rel="stylesheet" href="https://js.soufunimg.com/fangue/libs/check/css.css">'
        '<script src="https://js.soufunimg.com/fangue/libs/check/checkyzm.min.js"></script>'
        '</head><body><p style="text-align:center">请完成下列验证后继续：</p>'
        '<div class="slider"></div></body></html>'
    )
    with pytest.raises(FangSuggestBlockedError):
        parse_suggest_payload(captcha)

    with pytest.raises(FangSuggestBlockedError):
        parse_suggest_payload("")


def test_http_client_absorbs_and_injects_cookies_for_manual_rescue():
    """风控救援靠 cookie 回灌：Set-Cookie 合并 + 人工过验证后回灌都要生效。

    回灌必须同时把会话标为“未预热”，否则不会用新 cookie 重新预热。
    """
    from scripts.initialize_community_page.community_page.fang_community_page_mvp import (
        _FangHttpClient,
    )

    client = _FangHttpClient("https://sz.esf.fang.com")
    client._warmed = True

    client._absorb_cookies(
        ["szesf_fang=abc123; Path=/; Domain=.sz.esf.fang.com", "flag=1; HttpOnly"]
    )
    assert client._cookies == {"szesf_fang": "abc123", "flag": "1"}

    client.inject_cookies({"__jsl_clearance_s": "clear456"})
    assert client._cookies["__jsl_clearance_s"] == "clear456"
    assert client._cookies["szesf_fang"] == "abc123"  # 原有 cookie 不被冲掉
    assert client._warmed is False  # 回灌后强制重新预热

    client._warmed = True
    client.inject_cookies({})  # 空回灌不得破坏会话状态
    assert client._warmed is True
    assert client._cookies["szesf_fang"] == "abc123"


# ============================================================
# 空态/单条边界（基于真实 dump 校准，见 debug/20260903_1805_fang_listing_*）
# marker：空态页主体 shop_no 容器 + "很抱歉，没有找到…相符的房源"；
# 单条页也有 shop_no 但那是隐形弹窗（pop_up/close_pop），不能误判。
# ============================================================


def test_is_no_result_on_real_empty_page_marker():
    """真实空态页（莲通公司综合楼 house-xm2810134960）：三段关键 HTML。"""
    from app.rpa.platforms.fang.collector import is_no_result

    # 空态主体容器 + 同页仍渲染其他小区推荐 dl 卡
    empty_html = """
    <div class="shop_no"><dl><dt><img src="//static.soufunimg.com/esf/esf/online/esflistnew/static/images/icon_no.jpg"></dt><dd> 很抱歉，没有找到<span class='bold org'>莲通公司综合楼</span>相符的房源！ </dd></dl></div>
    <div class="shop_no"><div class="shade" style="display: none;"></div><div class="pop_up" style="display: none;">...</div></div>
    <dl class="clearfix"><dd><p class="add_shop"><a>鸣乐大厦</a></p></dd></dl>
    <dl class="clearfix"><dd><p class="add_shop"><a>莲丰大厦</a></p></dd></dl>
    """
    assert is_no_result(empty_html) is True


def test_is_no_result_false_on_single_result_page():
    """单条数据页（莲塘派出所宿舍 house-xm2811125778）：有 shop_no 弹窗但非空态。"""
    from app.rpa.platforms.fang.collector import is_no_result

    single_html = """
    <dl class="clearfix"><dd><p class="add_shop"><a href="/house-xm2811125778/" title="莲塘派出所宿舍"> 莲塘派出所宿舍 </a></p></dd></dl>
    <div class="shop_no"><div class="shade" style="display: none;"></div><div class="pop_up" style="display: none;"><i class="close_pop">x</i>...</div></div>
    """
    assert is_no_result(single_html) is False


def test_is_no_result_false_on_normal_listing_page():
    """正常挂牌页：无 shop_no 容器，多个在售卡。"""
    from app.rpa.platforms.fang.collector import is_no_result

    normal_html = """
    <dl class="clearfix"><dd><p class="add_shop"><a>佳兆业樾伴山</a></p></dd></dl>
    <dl class="clearfix"><dd><p class="add_shop"><a>佳兆业樾伴山</a></p></dd></dl>
    """
    assert is_no_result(normal_html) is False


def test_parse_listing_snapshots_cuts_recommended_listings():
    """单条/空态页房源少时页面补'您可能感兴趣的房源'推荐位（其他小区），
    parser 必须截断，否则他小区房源混入（dump 20260903_1805 单条样本）。"""
    html = """
    <dl class="clearfix">
      <dd><h4><a><span class="tit_shop">莲塘派出所宿舍 3室</span></a></h4>
          <p class="tel_shop">3室1厅 | 90.9㎡</p>
          <p class="add_shop"><a title="莲塘派出所宿舍">莲塘派出所宿舍</a></p></dd>
      <dd class="price_right"><span class="red"><b>300</b>万</span><span>33004元/㎡</span></dd>
    </dl>
    <p class="tit_x">您可能感兴趣的房源</p>
    <div class="shop_list">
      <dl class="clearfix">
        <dd><h4><a><span class="tit_shop">鸣乐大厦 3室</span></a></h4>
            <p class="tel_shop">3室2厅 | 84.16㎡</p>
            <p class="add_shop"><a title="鸣乐大厦">鸣乐大厦</a></p></dd>
        <dd class="price_right"><span class="red"><b>109</b>万</span><span>12951元/㎡</span></dd>
      </dl>
    </div>
    """
    snapshots = parse_listing_snapshots(html)

    assert len(snapshots) == 1
    assert snapshots[0].community_name == "莲塘派出所宿舍"
