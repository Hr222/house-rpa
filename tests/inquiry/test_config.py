# -*- coding: utf-8 -*-
"""RPA 运行配置测试。"""

from app.rpa.core import config


def test_browser_path_reads_environment_value(monkeypatch) -> None:
    monkeypatch.setenv("BROWSER_PATH", r"D:\Apps\Chrome\chrome.exe")

    assert config._browser_path_from_env() == r"D:\Apps\Chrome\chrome.exe"


def test_browser_path_falls_back_for_empty_environment_value(monkeypatch) -> None:
    monkeypatch.setenv("BROWSER_PATH", "   ")

    assert config._browser_path_from_env() == config.DEFAULT_BROWSER_PATH


def test_api_port_reads_valid_environment_value(monkeypatch) -> None:
    monkeypatch.setenv("API_PORT", "18000")

    assert config._api_port_from_env() == 18000


def test_api_port_falls_back_for_invalid_environment_value(monkeypatch) -> None:
    monkeypatch.setenv("API_PORT", "not-a-port")

    assert config._api_port_from_env() == config.DEFAULT_API_PORT


def test_get_inquiry_min_interval_reads_valid_environment_value(monkeypatch) -> None:
    monkeypatch.setenv("RPA_GET_MIN_INTERVAL", "2.5")

    assert config._get_inquiry_min_interval_from_env() == 2.5


def test_get_inquiry_min_interval_falls_back_for_invalid_environment_value(monkeypatch) -> None:
    monkeypatch.setenv("RPA_GET_MIN_INTERVAL", "not-a-number")

    assert (
        config._get_inquiry_min_interval_from_env()
        == config.DEFAULT_GET_INQUIRY_MIN_INTERVAL
    )


def test_get_inquiry_min_interval_falls_back_for_non_finite_or_negative_value(monkeypatch) -> None:
    for raw_value in ("nan", "inf", "-1"):
        monkeypatch.setenv("RPA_GET_MIN_INTERVAL", raw_value)

        assert (
            config._get_inquiry_min_interval_from_env()
            == config.DEFAULT_GET_INQUIRY_MIN_INTERVAL
        )
