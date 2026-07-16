# -*- coding: utf-8 -*-
"""房天下面积档位动态解析单元测试。

验证 parse_area_segments 能从结果页 HTML 正确读取各城市的面积档位。
房天下用"平米"单位，DOM 结构与安居客不同。
"""

from app.parsers.fang import parse_area_segments


# 深圳真实 HTML 片段（9 档）
SHENZHEN_HTML = """
<li class="clearfix screen_list"><span class="screen_title">面积</span>
<ul class="clearfix choose_screen">
<li><label><span class="icon_check"></span><a href="x">50平米以下</a></label></li>
<li><label><span class="icon_check"></span><a href="x">50-70平米</a></label></li>
<li><label><span class="icon_check"></span><a href="x">70-90平米</a></label></li>
<li><label><span class="icon_check"></span><a href="x">90-110平米</a></label></li>
<li><label><span class="icon_check"></span><a href="x">110-130平米</a></label></li>
<li><label><span class="icon_check"></span><a href="x">130-150平米</a></label></li>
<li><label><span class="icon_check"></span><a href="x">150-200平米</a></label></li>
<li><label><span class="icon_check"></span><a href="x">200-300平米</a></label></li>
<li><label><span class="icon_check"></span><a href="x">300平米以上</a></label></li>
<li class="text_inp" name="customarea"><input id="cminArea"> - <input id="cmaxArea"><input id="aConfirmButton" value="确定"></li>
</ul></li>
<li class="clearfix screen_list"><span class="screen_title">特色</span></li>
"""

# 汕尾真实 HTML 片段（8 档，90-110 和 110-130 合并成 90-130）
SHANWEI_HTML = """
<li class="clearfix screen_list"><span class="screen_title">面积</span>
<ul class="clearfix choose_screen">
<li><label><span class="icon_check"></span><a href="x">50平米以下</a></label></li>
<li><label><span class="icon_check"></span><a href="x">50-70平米</a></label></li>
<li><label><span class="icon_check"></span><a href="x">70-90平米</a></label></li>
<li><label><span class="icon_check"></span><a href="x">90-130平米</a></label></li>
<li><label><span class="icon_check"></span><a href="x">130-150平米</a></label></li>
<li><label><span class="icon_check"></span><a href="x">150-200平米</a></label></li>
<li><label><span class="icon_check"></span><a href="x">200-300平米</a></label></li>
<li><label><span class="icon_check"></span><a href="x">300平米以上</a></label></li>
<li class="text_inp" name="customarea"><input id="cminArea"> - <input id="cmaxArea"><input id="aConfirmButton" value="确定"></li>
</ul></li>
<li class="clearfix screen_list"><span class="screen_title">特色</span></li>
"""


def test_parse_shenzhen_segments():
    """深圳 9 档全部读取，数值正确。"""
    segments = parse_area_segments(SHENZHEN_HTML)
    assert len(segments) == 9
    assert segments[0] == ("50平米以下", 0.0, 50.0)
    assert segments[3] == ("90-110平米", 90.0, 110.0)
    assert segments[4] == ("110-130平米", 110.0, 130.0)
    assert segments[-1] == ("300平米以上", 300.0, float("inf"))


def test_parse_shanwei_segments():
    """汕尾 8 档，90-110 和 110-130 合并成 90-130。"""
    segments = parse_area_segments(SHANWEI_HTML)
    assert len(segments) == 8
    assert segments[3] == ("90-130平米", 90.0, 130.0)
    assert segments[-1] == ("300平米以上", 300.0, float("inf"))


def test_skip_custom_input():
    """自定义输入框被跳过。"""
    segments = parse_area_segments(SHENZHEN_HTML)
    texts = [s[0] for s in segments]
    # 不含"确定"等自定义输入框内容
    assert all("确定" not in t for t in texts)


def test_empty_when_no_area_section():
    """页面没有面积筛选区时返回空列表。"""
    assert parse_area_segments("<html>无面积筛选</html>") == []
