"""
Tests for BaseAW: locator resolution, retry decorator, helper methods.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch, call

import pytest
import yaml

from uiAutoAgent.aw.base_aw import BaseAW, retry
from uiAutoAgent.executor.device_manager import DeviceManager
from uiAutoAgent.executor.drivers.base_driver import BaseDriver


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

class MockDriver(BaseDriver):
    def __init__(self, serial: str):
        super().__init__(serial)
        self._elements: Dict[str, MagicMock] = {}

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def find_element_with_fallback(self, strategies: List[Dict[str, str]]) -> Any:
        # Return a mock element for any strategy
        elem = MagicMock()
        elem.text = "mock_text"
        elem.is_displayed.return_value = True
        elem.get_attribute.return_value = "false"
        return elem

    def find_element(self, strategy: str, value: str) -> Any:
        elem = MagicMock()
        elem.text = "mock_text"
        return elem

    def is_element_visible(self, strategy: str, value: str) -> bool:
        return True

    def wait_for_element(self, strategy: str, value: str, timeout: int = 10) -> Any:
        return MagicMock()

    def run_command(self, command: str, timeout: int = 30) -> str:
        return "ok"

    def screenshot(self, path: str) -> str:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"PNG_FAKE_DATA")
        return path


@pytest.fixture
def locator_root(tmp_path: Path) -> Path:
    v1 = tmp_path / "v1.0"
    v1.mkdir()
    data = {
        "version": "1.0",
        "page": "test",
        "elements": {
            "submit_btn": {
                "description": "Submit button",
                "strategies": [
                    {"strategy": "accessibility_id", "value": "submit"},
                    {"strategy": "xpath", "value": "//Button[@text='Submit']"},
                ],
            },
            "name_input": {
                "description": "Name field",
                "strategies": [
                    {"strategy": "id", "value": "com.example:id/et_name"},
                ],
            },
        },
    }
    (v1 / "test_page.yaml").write_text(yaml.dump(data), encoding="utf-8")
    return tmp_path


@pytest.fixture
def dm() -> DeviceManager:
    manager = DeviceManager()
    manager.register("test-device", lambda: MockDriver("test-device"))
    return manager


class _AWForTest(BaseAW):
    PAGE = "test"

    def __init__(self, dm: DeviceManager, locator_root: Path):
        # Bypass the _detect_app_version adb call
        super().__init__(dm, "test-device", app_version="1.0")
        # Override locator root
        from uiAutoAgent.core import LocatorManager
        self._locator_manager = LocatorManager("1.0", locator_root=str(locator_root))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBaseAWLocators:
    def test_get_strategies(self, dm, locator_root):
        aw = _AWForTest(dm, locator_root)
        strategies = aw._get_strategies("submit_btn")
        assert len(strategies) == 2
        assert any(s["strategy"] == "accessibility_id" for s in strategies)

    def test_best_strategy_prefers_accessibility_id(self, dm, locator_root):
        aw = _AWForTest(dm, locator_root)
        best = aw._best_strategy("submit_btn")
        assert best["strategy"] == "accessibility_id"

    def test_missing_element_raises(self, dm, locator_root):
        aw = _AWForTest(dm, locator_root)
        with pytest.raises(KeyError):
            aw._get_strategies("nonexistent_btn")


class TestBaseAWActions:
    def test_tap_calls_click(self, dm, locator_root):
        aw = _AWForTest(dm, locator_root)
        # Should not raise
        aw.tap("submit_btn")

    def test_type_text(self, dm, locator_root):
        aw = _AWForTest(dm, locator_root)
        aw.type_text("name_input", "hello")

    def test_is_visible_returns_bool(self, dm, locator_root):
        aw = _AWForTest(dm, locator_root)
        result = aw.is_visible("submit_btn")
        assert isinstance(result, bool)

    def test_screenshot_returns_path(self, dm, locator_root, tmp_path):
        aw = _AWForTest(dm, locator_root)
        path = aw.screenshot(str(tmp_path / "test.png"))
        assert Path(path).exists()


class TestRetryDecorator:
    def test_succeeds_first_try(self):
        call_count = 0

        @retry(max_attempts=3, delay=0)
        def flaky_func():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = flaky_func()
        assert result == "ok"
        assert call_count == 1

    def test_retries_on_exception(self):
        call_count = 0

        @retry(max_attempts=3, delay=0)
        def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("temporary error")
            return "ok"

        result = flaky_func()
        assert result == "ok"
        assert call_count == 3

    def test_raises_after_max_attempts(self):
        @retry(max_attempts=2, delay=0)
        def always_fails():
            raise RuntimeError("permanent failure")

        with pytest.raises(RuntimeError, match="permanent failure"):
            always_fails()

    def test_only_retries_specified_exceptions(self):
        call_count = 0

        @retry(max_attempts=3, delay=0, exceptions=(ValueError,))
        def func():
            nonlocal call_count
            call_count += 1
            raise RuntimeError("unexpected error")

        with pytest.raises(RuntimeError):
            func()
        assert call_count == 1  # Should not retry RuntimeError
