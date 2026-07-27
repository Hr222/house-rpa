# -*- coding: utf-8 -*-
"""房天下风控规则回归测试。"""

from app.platforms.adapters.fang import detect_block


def test_normal_detail_sms_code_form_is_not_captcha():
    """详情页的手机号短信验证码输入框不是平台风控页。"""
    html = """
    <html><body>
      <h1>景龙大厦小区详情</h1>
      <div class="proving_item">
        <input id="yyinputmobile" placeholder="请输入手机号">
        <input id="yyinputmobilecode" placeholder="请输入验证码">
      </div>
      <a href="/loupan/123/chengjiao/">小区成交</a>
    </body></html>
    """

    assert detect_block("https://sz.esf.fang.com/loupan/123/", html) == (False, "")


def test_fang_human_verification_page_is_detected():
    html = "<html><body>访问过于频繁，请完成验证后继续访问</body></html>"

    blocked, reason = detect_block("https://sz.esf.fang.com/house/", html)

    assert blocked is True
    assert reason == "命中验证码拦截"
