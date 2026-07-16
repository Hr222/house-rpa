# -*- coding: utf-8 -*-
"""链家面积档位动态解析单元测试。

链家 DOM 与贝壳一致（同代码库），唯一差异：标题是"面积"（贝壳是"建筑面积"）。
"""

from app.parsers.lj import parse_area_segments


# 深圳真实 HTML 片段（8 档）
SHENZHEN_HTML = """
<dl class="hide hasmore">
<dt>面积</dt>
<dd>
<a href="https://sz.lianjia.com/ershoufang/a1/"><span class="checkbox"></span><span class="name">50㎡以下</span></a>
<a href="https://sz.lianjia.com/ershoufang/a2/"><span class="checkbox"></span><span class="name">50-70㎡</span></a>
<a href="https://sz.lianjia.com/ershoufang/a3/"><span class="checkbox"></span><span class="name">70-90㎡</span></a>
<a href="https://sz.lianjia.com/ershoufang/a4/"><span class="checkbox"></span><span class="name">90-110㎡</span></a>
<a href="https://sz.lianjia.com/ershoufang/a5/"><span class="checkbox"></span><span class="name">110-140㎡</span></a>
<a href="https://sz.lianjia.com/ershoufang/a6/"><span class="checkbox"></span><span class="name">140-170㎡</span></a>
<a href="https://sz.lianjia.com/ershoufang/a7/"><span class="checkbox"></span><span class="name">170-200㎡</span></a>
<a href="https://sz.lianjia.com/ershoufang/a8/"><span class="checkbox"></span><span class="name">200㎡以上</span></a>
<span class="customFilter" data-role="area"><input><button>确定</button></span>
</dd>
</dl>
"""

# 佛山真实 HTML 片段（7 档）
FOSHAN_HTML = """
<dl class="hide hasmore">
<dt>面积</dt>
<dd>
<a href="https://fs.lianjia.com/ershoufang/p8/"><span class="checkbox"></span><span class="name">40㎡以下</span></a>
<a href="https://fs.lianjia.com/ershoufang/a2/"><span class="checkbox"></span><span class="name">40-60㎡</span></a>
<a href="https://fs.lianjia.com/ershoufang/a3/"><span class="checkbox"></span><span class="name">60-80㎡</span></a>
<a href="https://fs.lianjia.com/ershoufang/a4/"><span class="checkbox"></span><span class="name">80-100㎡</span></a>
<a href="https://fs.lianjia.com/ershoufang/a5/"><span class="checkbox"></span><span class="name">100-120㎡</span></a>
<a href="https://fs.lianjia.com/ershoufang/a6/"><span class="checkbox"></span><span class="name">120-160㎡</span></a>
<a href="https://fs.lianjia.com/ershoufang/a7/"><span class="checkbox"></span><span class="name">160㎡以上</span></a>
<span class="customFilter" data-role="area"><input><button>确定</button></span>
</dd>
</dl>
"""


def test_parse_shenzhen_segments():
    """深圳 8 档全部读取。"""
    segments = parse_area_segments(SHENZHEN_HTML)
    assert len(segments) == 8
    assert segments[0] == ("50㎡以下", 0.0, 50.0)
    assert segments[2] == ("70-90㎡", 70.0, 90.0)
    assert segments[-1] == ("200㎡以上", 200.0, float("inf"))


def test_parse_foshan_segments():
    """佛山 7 档，首档 40（深圳是 50）。"""
    segments = parse_area_segments(FOSHAN_HTML)
    assert len(segments) == 7
    assert segments[0] == ("40㎡以下", 0.0, 40.0)
    assert segments[-1] == ("160㎡以上", 160.0, float("inf"))


def test_skip_custom_input():
    """自定义输入框被跳过。"""
    segments = parse_area_segments(SHENZHEN_HTML)
    texts = [s[0] for s in segments]
    assert all("确定" not in t for t in texts)


def test_empty_when_no_area_section():
    """页面没有面积筛选区时返回空列表。"""
    assert parse_area_segments("<html>无面积筛选</html>") == []
