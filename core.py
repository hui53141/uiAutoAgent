"""
uiAutoAgent core utilities: config loading, logging, locator management.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).parent
_settings_cache: Optional[Dict[str, Any]] = None


def get_settings(path: Optional[str] = None) -> Dict[str, Any]:
    """Load and cache settings.yaml (or a custom path)."""
    global _settings_cache
    if _settings_cache is not None:
        return _settings_cache
    cfg_path = Path(path) if path else _ROOT / "configs" / "settings.yaml"
    with open(cfg_path, "r", encoding="utf-8") as fh:
        _settings_cache = yaml.safe_load(fh)
    return _settings_cache


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(name: str = "uiAutoAgent") -> logging.Logger:
    """Configure and return a named logger using settings.yaml."""
    settings = get_settings()
    log_cfg = settings.get("logging", {})
    level = getattr(logging, log_cfg.get("level", "INFO"), logging.INFO)
    fmt = log_cfg.get("format", "%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    log_file = log_cfg.get("file", "/tmp/uiAutoAgent/logs/uiAutoAgent.log")

    log_dir = Path(log_file).parent
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(level)
    formatter = logging.Formatter(fmt)

    # Console handler
    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # Rotating file handler
    fh = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=log_cfg.get("max_bytes", 10 * 1024 * 1024),
        backupCount=log_cfg.get("backup_count", 5),
        encoding="utf-8",
    )
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    return logger


# ---------------------------------------------------------------------------
# Locator manager
# ---------------------------------------------------------------------------

class LocatorManager:
    """
    Loads versioned locator YAML files and resolves element strategies.

    Supports three-level fallback:
      L1 - accessibility_id  (most stable)
      L2 - id / xpath        (semantic fallback)
      L3 - image             (visual template, last resort)
    """

    FALLBACK_ORDER = ["accessibility_id", "id", "xpath", "image"]

    def __init__(self, app_version: str, locator_root: Optional[str] = None):
        self.app_version = app_version
        settings = get_settings()
        root = locator_root or settings.get("locator", {}).get("locator_root", "locators")
        self._root = _ROOT / root
        self._cache: Dict[str, Dict[str, Any]] = {}
        self.logger = setup_logging("LocatorManager")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, page: str, element: str) -> Dict[str, Any]:
        """
        Return the best available locator dict for *element* on *page*.

        Returns:
            {"strategy": "accessibility_id", "value": "some_id"}
        """
        page_data = self._load_page(page)
        elements = page_data.get("elements", {})
        if element not in elements:
            raise KeyError(f"Element '{element}' not found in page '{page}' locators")

        strategies: List[Dict[str, str]] = elements[element].get("strategies", [])
        return self._pick_best(strategies)

    def get_all_strategies(self, page: str, element: str) -> List[Dict[str, str]]:
        """Return all strategies for an element (for fallback iteration).

        Raises:
            KeyError: If the element is not defined in the locator file.
        """
        page_data = self._load_page(page)
        elements = page_data.get("elements", {})
        if element not in elements:
            raise KeyError(f"Element '{element}' not found in page '{page}' locators")
        return elements[element].get("strategies", [])

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_page(self, page: str) -> Dict[str, Any]:
        if page in self._cache:
            return self._cache[page]

        version_dir = self._resolve_version_dir()
        page_file = version_dir / f"{page}_page.yaml"
        if not page_file.exists():
            raise FileNotFoundError(
                f"Locator file not found: {page_file}. "
                f"Available: {list(version_dir.glob('*.yaml'))}"
            )

        with open(page_file, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        self._cache[page] = data
        self.logger.debug("Loaded locators for page '%s' from %s", page, page_file)
        return data

    def _resolve_version_dir(self) -> Path:
        """
        Find the highest versioned directory that is <= app_version.
        Supports semantic versioning comparison (major.minor).
        """
        available = sorted(
            [d for d in self._root.iterdir() if d.is_dir()],
            key=lambda d: self._version_tuple(d.name),
        )
        if not available:
            raise FileNotFoundError(f"No version directories found in {self._root}")

        app_tuple = self._version_tuple(self.app_version)
        selected = available[0]  # fallback: oldest
        for d in available:
            if self._version_tuple(d.name) <= app_tuple:
                selected = d
        return selected

    @staticmethod
    def _version_tuple(version: str) -> tuple:
        """Convert version string like 'v1.2.3' or '1.2' to comparable tuple."""
        cleaned = re.sub(r"[^0-9.]", "", version)
        try:
            return tuple(int(x) for x in cleaned.split(".") if x)
        except ValueError:
            return (0,)

    def _pick_best(self, strategies: List[Dict[str, str]]) -> Dict[str, str]:
        """Pick the highest-priority strategy from the fallback order."""
        order_map = {s: i for i, s in enumerate(self.FALLBACK_ORDER)}
        sorted_strategies = sorted(
            strategies,
            key=lambda s: order_map.get(s.get("strategy", ""), 99),
        )
        if not sorted_strategies:
            raise ValueError("No strategies defined for element")
        return sorted_strategies[0]
