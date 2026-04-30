"""
Tests for LocatorManager: versioned YAML loading + fallback strategy.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from uiAutoAgent.core import LocatorManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def locator_root(tmp_path: Path) -> Path:
    """Create a temporary locator directory with two versions."""
    v1 = tmp_path / "v1.0"
    v1.mkdir()
    v2 = tmp_path / "v2.0"
    v2.mkdir()

    login_v1 = {
        "version": "1.0",
        "page": "login",
        "elements": {
            "login_button": {
                "description": "Login button",
                "strategies": [
                    {"strategy": "accessibility_id", "value": "login_btn_v1"},
                    {"strategy": "xpath", "value": "//Button[@text='Login']"},
                ],
            },
            "username_input": {
                "description": "Username field",
                "strategies": [
                    {"strategy": "id", "value": "com.example:id/et_user_v1"},
                ],
            },
        },
    }
    login_v2 = {
        "version": "2.0",
        "page": "login",
        "elements": {
            "login_button": {
                "description": "Login button (redesigned)",
                "strategies": [
                    {"strategy": "accessibility_id", "value": "btn_login_v2"},
                    {"strategy": "id", "value": "com.example:id/btn_v2"},
                ],
            },
            "username_input": {
                "description": "Username field (now phone number)",
                "strategies": [
                    {"strategy": "accessibility_id", "value": "phone_input"},
                ],
            },
        },
    }

    (v1 / "login_page.yaml").write_text(yaml.dump(login_v1), encoding="utf-8")
    (v2 / "login_page.yaml").write_text(yaml.dump(login_v2), encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestVersionResolution:
    def test_exact_version_match(self, locator_root: Path):
        lm = LocatorManager("1.0", locator_root=str(locator_root))
        best = lm.get("login", "login_button")
        assert best["value"] == "login_btn_v1"

    def test_higher_version_resolved(self, locator_root: Path):
        lm = LocatorManager("2.0", locator_root=str(locator_root))
        best = lm.get("login", "login_button")
        assert best["value"] == "btn_login_v2"

    def test_version_between_releases_uses_lower(self, locator_root: Path):
        """App version 1.5 → should use v1.0 locators (not v2.0)."""
        lm = LocatorManager("1.5", locator_root=str(locator_root))
        best = lm.get("login", "login_button")
        assert best["value"] == "login_btn_v1"

    def test_version_prefix_handling(self, locator_root: Path):
        """Version strings like 'v2.0' should be parsed correctly."""
        lm = LocatorManager("v2.0", locator_root=str(locator_root))
        best = lm.get("login", "login_button")
        assert best["strategy"] == "accessibility_id"

    def test_missing_page_raises(self, locator_root: Path):
        lm = LocatorManager("1.0", locator_root=str(locator_root))
        with pytest.raises(FileNotFoundError):
            lm.get("settings", "wifi_toggle")

    def test_missing_element_raises(self, locator_root: Path):
        lm = LocatorManager("1.0", locator_root=str(locator_root))
        with pytest.raises(KeyError):
            lm.get("login", "nonexistent_element")


class TestFallbackOrder:
    def test_accessibility_id_preferred(self, locator_root: Path):
        """accessibility_id should always be selected over xpath/id."""
        lm = LocatorManager("1.0", locator_root=str(locator_root))
        best = lm.get("login", "login_button")
        assert best["strategy"] == "accessibility_id"

    def test_id_preferred_over_xpath(self, locator_root: Path):
        """When no accessibility_id, id is preferred over xpath."""
        lm = LocatorManager("2.0", locator_root=str(locator_root))
        # v2.0 login_button has both accessibility_id and id; accessibility_id wins
        best = lm.get("login", "login_button")
        assert best["strategy"] == "accessibility_id"

    def test_all_strategies_returned(self, locator_root: Path):
        lm = LocatorManager("1.0", locator_root=str(locator_root))
        strategies = lm.get_all_strategies("login", "login_button")
        assert len(strategies) == 2
        strategies_names = [s["strategy"] for s in strategies]
        assert "accessibility_id" in strategies_names
        assert "xpath" in strategies_names


class TestCaching:
    def test_page_is_cached(self, locator_root: Path):
        lm = LocatorManager("1.0", locator_root=str(locator_root))
        lm.get("login", "login_button")
        lm.get("login", "username_input")  # second access should use cache
        assert "login" in lm._cache

    def test_different_versions_independent_cache(self, locator_root: Path):
        lm1 = LocatorManager("1.0", locator_root=str(locator_root))
        lm2 = LocatorManager("2.0", locator_root=str(locator_root))
        v1_val = lm1.get("login", "login_button")["value"]
        v2_val = lm2.get("login", "login_button")["value"]
        assert v1_val != v2_val
