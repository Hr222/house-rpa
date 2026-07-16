# -*- coding: utf-8 -*-
"""安居客面积档位动态解析单元测试。

验证 parse_area_segments 能从结果页 HTML 正确读取各城市的面积档位。
"""

from app.parsers.ajk import parse_area_segments


# 深圳真实 HTML 片段（9 档）
SHENZHEN_HTML = """
<div class="filter-wrap"><span class="filter-title filter-title-line">面积</span>
<section class="filter-content"><ul class="line">
<li class="line-item line-item-active"><a href="x">不限</a></li>
<li class="line-item"><a href="x">50㎡以下</a></li>
<li class="line-item"><a href="x">50-60㎡</a></li>
<li class="line-item"><a href="x">60-70㎡</a></li>
<li class="line-item"><a href="x">70-80㎡</a></li>
<li class="line-item"><a href="x">80-90㎡</a></li>
<li class="line-item"><a href="x">90-100㎡</a></li>
<li class="line-item"><a href="x">100-130㎡</a></li>
<li class="line-item"><a href="x">130-190㎡</a></li>
<li class="line-item"><a href="x">190㎡以上</a></li>
<li class="line-item line-item-input"><input><span>确定</span></li>
</ul></section></div>
<div class="filter-wrap"><span class="filter-title filter-title-line">房型</span></div>
"""

# 汕尾真实 HTML 片段（8 档，数值完全不同）
SHANWEI_HTML = """
<div class="filter-wrap"><span class="filter-title filter-title-line">面积</span>
<section class="filter-content"><ul class="line">
<li class="line-item line-item-active"><a href="x">不限</a></li>
<li class="line-item"><a href="x">70㎡以下</a></li>
<li class="line-item"><a href="x">70-100㎡</a></li>
<li class="line-item"><a href="x">100-110㎡</a></li>
<li class="line-item"><a href="x">110-120㎡</a></li>
<li class="line-item"><a href="x">120-130㎡</a></li>
<li class="line-item"><a href="x">130-140㎡</a></li>
<li class="line-item"><a href="x">140-150㎡</a></li>
<li class="line-item"><a href="x">150㎡以上</a></li>
<li class="line-item line-item-input"><input><span>确定</span></li>
</ul></section></div>
<div class="filter-wrap"><span class="filter-title filter-title-line">房型</span></div>
"""


def test_parse_shenzhen_segments():
    """深圳 9 档全部读取，数值正确。"""
    segments = parse_area_segments(SHENZHEN_HTML)
    assert len(segments) == 9
    assert segments[0] == ("50㎡以下", 0.0, 50.0)
    assert segments[4] == ("80-90㎡", 80.0, 90.0)
    assert segments[-1] == ("190㎡以上", 190.0, float("inf"))


def test_parse_shanwei_segments():
    """汕尾 8 档，数值与深圳完全不同。"""
    segments = parse_area_segments(SHANWEI_HTML)
    assert len(segments) == 8
    assert segments[0] == ("70㎡以下", 0.0, 70.0)
    assert segments[1] == ("70-100㎡", 70.0, 100.0)
    assert segments[-1] == ("150㎡以上", 150.0, float("inf"))


def test_skip_unlimited_and_input():
    """不限和自定义输入框被跳过。"""
    segments = parse_area_segments(SHENZHEN_HTML)
    texts = [s[0] for s in segments]
    assert "不限" not in texts


def test_empty_when_no_area_section():
    """页面没有面积筛选区时返回空列表。"""
    assert parse_area_segments("<html>无面积筛选</html>") == []
