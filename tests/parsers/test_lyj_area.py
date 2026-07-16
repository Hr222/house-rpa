# -*- coding: utf-8 -*-
"""乐有家面积档位动态解析单元测试。

验证 parse_area_segments 能从结果页 HTML 正确读取各城市的面积档位。
乐有家用"㎡"单位，档位文本在 <span class="name"> 里。
"""

from app.parsers.lyj import parse_area_segments


# 深圳真实 HTML 片段（8 档）
SHENZHEN_HTML = """
<div class="selected-index multi-select hasmore">
<span class="c333 tit">面积</span>
<a href="x" class="xx5"><span class="checkbox"></span><span class="name">30㎡以下</span></a>
<a href="x" class="xx5"><span class="checkbox"></span><span class="name">30-50㎡</span></a>
<a href="x" class="xx5"><span class="checkbox"></span><span class="name">50-80㎡</span></a>
<a href="x" class="xx5"><span class="checkbox"></span><span class="name">80-100㎡</span></a>
<a href="x" class="xx5"><span class="checkbox"></span><span class="name">100-120㎡</span></a>
<a href="x" class="xx5"><span class="checkbox"></span><span class="name">120-150㎡</span></a>
<a href="x" class="xx5"><span class="checkbox"></span><span class="name">150-200㎡</span></a>
<a href="x" class="xx5"><span class="checkbox"></span><span class="name">200㎡以上</span></a>
<span class="selected-input"><input id="a_start"> - <input id="a_end">㎡<button id="areaUsedefinedBtn">确定</button></span>
<span class="btn-showmore"><em>+</em> 更多及自定义</span>
</div>
<div class="selected-index multi-select"><span class="c333 tit">户型</span></div>
"""

# 佛山真实 HTML 片段（8 档，与深圳相同但验证城市无关性）
FOSHAN_HTML = """
<div class="selected-index multi-select hasmore">
<span class="c333 tit">面积</span>
<a href="x" class="xx5"><span class="checkbox"></span><span class="name">30㎡以下</span></a>
<a href="x" class="xx5"><span class="checkbox"></span><span class="name">30-50㎡</span></a>
<a href="x" class="xx5"><span class="checkbox"></span><span class="name">50-80㎡</span></a>
<a href="x" class="xx5"><span class="checkbox"></span><span class="name">80-100㎡</span></a>
<a href="x" class="xx5"><span class="checkbox"></span><span class="name">100-120㎡</span></a>
<a href="x" class="xx5"><span class="checkbox"></span><span class="name">120-150㎡</span></a>
<a href="x" class="xx5"><span class="checkbox"></span><span class="name">150-200㎡</span></a>
<a href="x" class="xx5"><span class="checkbox"></span><span class="name">200㎡以上</span></a>
<span class="selected-input"><input id="a_start"> - <input id="a_end">㎡<button id="areaUsedefinedBtn">确定</button></span>
</div>
<div class="selected-index multi-select"><span class="c333 tit">户型</span></div>
"""


def test_parse_shenzhen_segments():
    """深圳 8 档全部读取，数值正确。"""
    segments = parse_area_segments(SHENZHEN_HTML)
    assert len(segments) == 8
    assert segments[0] == ("30㎡以下", 0.0, 30.0)
    assert segments[3] == ("80-100㎡", 80.0, 100.0)
    assert segments[-1] == ("200㎡以上", 200.0, float("inf"))


def test_parse_foshan_segments():
    """佛山 8 档（与深圳相同，验证城市无关性）。"""
    segments = parse_area_segments(FOSHAN_HTML)
    assert len(segments) == 8
    assert segments[-1] == ("200㎡以上", 200.0, float("inf"))


def test_skip_custom_input():
    """自定义输入框被跳过。"""
    segments = parse_area_segments(SHENZHEN_HTML)
    texts = [s[0] for s in segments]
    assert all("确定" not in t for t in texts)
    assert all("input" not in t.lower() for t in texts)


def test_empty_when_no_area_section():
    """页面没有面积筛选区时返回空列表。"""
    assert parse_area_segments("<html>无面积筛选</html>") == []
