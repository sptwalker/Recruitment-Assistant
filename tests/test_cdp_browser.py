"""Tests for CDP browser module."""

import socket
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from recruitment_assistant.core.cdp_browser import (
    find_chrome_executable,
    _is_port_in_use,
    _wait_for_cdp_ready,
)


class TestFindChromeExecutable:
    def test_finds_chrome_at_default_path(self, tmp_path):
        fake_chrome = tmp_path / "chrome.exe"
        fake_chrome.touch()
        with patch(
            "recruitment_assistant.core.cdp_browser._DEFAULT_CHROME_PATHS",
            [fake_chrome],
        ), patch(
            "recruitment_assistant.core.cdp_browser.get_settings",
            return_value=MagicMock(chrome_executable_path=None),
        ):
            result = find_chrome_executable()
            assert result == fake_chrome

    def test_uses_custom_path_from_settings(self, tmp_path):
        fake_chrome = tmp_path / "my_chrome.exe"
        fake_chrome.touch()
        with patch(
            "recruitment_assistant.core.cdp_browser.get_settings",
            return_value=MagicMock(chrome_executable_path=str(fake_chrome)),
        ):
            result = find_chrome_executable()
            assert result == fake_chrome

    def test_raises_if_custom_path_not_found(self):
        with patch(
            "recruitment_assistant.core.cdp_browser.get_settings",
            return_value=MagicMock(chrome_executable_path="/nonexistent/chrome.exe"),
        ):
            with pytest.raises(FileNotFoundError, match="指定的 Chrome 路径不存在"):
                find_chrome_executable()

    def test_raises_if_no_chrome_found(self):
        with patch(
            "recruitment_assistant.core.cdp_browser._DEFAULT_CHROME_PATHS",
            [Path("/nonexistent/a.exe"), Path("/nonexistent/b.exe")],
        ), patch(
            "recruitment_assistant.core.cdp_browser.get_settings",
            return_value=MagicMock(chrome_executable_path=None),
        ):
            with pytest.raises(FileNotFoundError, match="未找到 Chrome 浏览器"):
                find_chrome_executable()


class TestIsPortInUse:
    def test_free_port_returns_false(self):
        assert _is_port_in_use(59999) is False

    def test_occupied_port_returns_true(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        try:
            assert _is_port_in_use(port) is True
        finally:
            server.close()


class TestWaitForCdpReady:
    def test_returns_true_when_port_ready(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        try:
            assert _wait_for_cdp_ready(port, timeout=2.0) is True
        finally:
            server.close()

    def test_returns_false_on_timeout(self):
        assert _wait_for_cdp_ready(59998, timeout=1.0) is False
