# -*- coding: utf-8 -*-
"""公共风控标识和平台规则合并入口测试。"""

from app.platforms.base import (
    detect_block_with_common,
    detect_common_block,
    is_manual_verify_reason,
)


def test_common_risk_url_marker_is_detected():
    blocked, reason = detect_common_block(
        "https://example.test/verifycode?token=1",
        "<html><body>请稍候</body></html>",
    )

    assert blocked is True
    assert "公共URL标识" in reason


def test_common_risk_html_marker_is_detected_without_business_content():
    blocked, reason = detect_common_block(
        "https://example.test/list",
        "<html><body>访问过于频繁，请完成验证</body></html>",
    )

    assert blocked is True
    assert "公共HTML标识" in reason


def test_common_risk_html_does_not_flag_normal_business_page():
    blocked, _ = detect_common_block(
        "https://example.test/list",
        "<html><body>在售房源 50000 元/㎡，小区均价 48000</body></html>",
    )

    assert blocked is False


def test_platform_specific_rule_has_priority_over_common_fallback():
    def platform_detector(url: str, html: str) -> tuple[bool, str]:
        return True, "平台专属安全标识"

    blocked, reason = detect_block_with_common(
        platform_detector,
        "https://example.test/captcha",
        "<html><body>验证码</body></html>",
    )

    assert blocked is True
    assert reason == "平台专属安全标识"


def test_manual_verify_reason_is_classified_for_health_state():
    assert is_manual_verify_reason("命中验证码拦截(公共URL标识)") is True
    assert is_manual_verify_reason("命中人机验证，等待人工处理") is True
    assert is_manual_verify_reason("未检测到已登录标识") is False
