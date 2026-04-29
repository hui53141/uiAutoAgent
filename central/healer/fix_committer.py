"""
FixCommitter: applies LLM-proposed locator fixes to YAML files and
commits + pushes them to GitHub, so all executor nodes receive the fix
on their next git pull.

This is the final step in the self-healing pipeline:
  FailureAggregator → ScreenshotAnalyzer → FixCommitter
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from uiAutoAgent.core import get_settings, setup_logging

logger = setup_logging("FixCommitter")

_ROOT = Path(__file__).parent.parent.parent  # project root


class FixCommitter:
    """
    Applies locator fixes to YAML files and commits them to GitHub.

    Pipeline:
      1. Load the relevant locator YAML for the affected page
      2. Merge proposed_strategies into the element definition
      3. Write the updated YAML back to disk
      4. git add + commit + push
    """

    def __init__(self, dry_run: bool = False):
        settings = get_settings()
        self.dry_run = dry_run
        cfg = settings["central"]
        self.branch = cfg.get("github_branch", "main")
        token = os.getenv(cfg.get("github_token_env", "GITHUB_TOKEN"), "")
        repo = cfg.get("github_repo", "")
        self.remote_url = (
            f"https://{token}@github.com/{repo}.git" if token and repo else ""
        )

    def apply_fix(
        self,
        fingerprint: str,
        page: str,
        element: str,
        proposed_strategies: List[Dict[str, str]],
        diagnosis: str,
        app_version: str = "v1.0",
    ) -> bool:
        """
        Apply a locator fix and commit it.

        Returns:
            True if the fix was committed successfully, False otherwise.
        """
        if not proposed_strategies:
            logger.warning(
                "No strategies proposed for fp=%s element=%s; skipping.",
                fingerprint,
                element,
            )
            return False

        locator_path = self._find_locator_file(page, app_version)
        if locator_path is None:
            logger.error(
                "Could not find locator file for page=%s version=%s",
                page,
                app_version,
            )
            return False

        try:
            updated = self._patch_locator(locator_path, element, proposed_strategies)
            if not updated:
                logger.info("No changes needed for %s/%s.", page, element)
                return False

            commit_msg = (
                f"fix(locators): auto-heal {page}/{element} [fp={fingerprint[:8]}]\n\n"
                f"Diagnosis: {diagnosis}\n"
                f"Updated strategies: {[s['strategy'] for s in proposed_strategies]}"
            )
            return self._commit_and_push(locator_path, commit_msg)
        except Exception as exc:
            logger.error("FixCommitter.apply_fix failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Locator patching
    # ------------------------------------------------------------------

    def _find_locator_file(self, page: str, app_version: str) -> Optional[Path]:
        """Find the best-matching locator file for a given page and version."""
        locator_root = _ROOT / "locators"
        version_dirs = sorted(
            [d for d in locator_root.iterdir() if d.is_dir()],
            key=lambda d: self._version_tuple(d.name),
        )
        if not version_dirs:
            return None

        # Find highest version dir <= app_version
        target_tuple = self._version_tuple(app_version)
        selected = version_dirs[0]
        for d in version_dirs:
            if self._version_tuple(d.name) <= target_tuple:
                selected = d

        page_file = selected / f"{page}_page.yaml"
        return page_file if page_file.exists() else None

    def _patch_locator(
        self,
        locator_path: Path,
        element: str,
        proposed_strategies: List[Dict[str, str]],
    ) -> bool:
        """
        Merge proposed strategies into the element's strategy list.
        Prepends the AI-proposed strategies (highest confidence first).

        Returns True if any change was made.
        """
        with open(locator_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)

        elements = data.get("elements", {})
        if element not in elements:
            logger.warning(
                "Element '%s' not in %s; cannot patch.", element, locator_path
            )
            return False

        existing = elements[element].get("strategies", [])
        # Remove duplicates (keep proposed at top)
        proposed_values = {s["value"] for s in proposed_strategies}
        surviving = [s for s in existing if s.get("value") not in proposed_values]
        merged = proposed_strategies + surviving

        if merged == existing:
            return False  # No change

        elements[element]["strategies"] = merged
        elements[element].setdefault("description", f"Auto-healed: {element}")

        with open(locator_path, "w", encoding="utf-8") as fh:
            yaml.dump(data, fh, allow_unicode=True, sort_keys=False)

        logger.info(
            "Patched locator: %s / %s  (%d strategies)",
            locator_path.name,
            element,
            len(merged),
        )
        return True

    # ------------------------------------------------------------------
    # Git operations
    # ------------------------------------------------------------------

    def _commit_and_push(self, changed_file: Path, commit_msg: str) -> bool:
        rel_path = changed_file.relative_to(_ROOT)

        if self.dry_run:
            logger.info("[DRY RUN] Would commit: %s\nMessage: %s", rel_path, commit_msg)
            return True

        try:
            self._git(["add", str(rel_path)])
            self._git(["commit", "-m", commit_msg])
            if self.remote_url:
                self._git(["push", self.remote_url, f"HEAD:{self.branch}"])
                logger.info("Pushed locator fix to GitHub branch '%s'.", self.branch)
            else:
                logger.warning(
                    "No GitHub remote URL configured; commit is local only. "
                    "Set GITHUB_TOKEN and central.github_repo in settings.yaml."
                )
            return True
        except subprocess.CalledProcessError as exc:
            logger.error("Git operation failed: %s\n%s", exc, exc.stderr)
            return False

    @staticmethod
    def _git(args: List[str]) -> str:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            cwd=str(_ROOT),
            check=True,
        )
        return result.stdout

    @staticmethod
    def _version_tuple(version: str) -> tuple:
        import re
        cleaned = re.sub(r"[^0-9.]", "", version)
        try:
            return tuple(int(x) for x in cleaned.split(".") if x)
        except ValueError:
            return (0,)
