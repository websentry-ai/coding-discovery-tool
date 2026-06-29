"""
Augment Code detection for macOS.

Augment Code ships three surfaces that share one ``~/.augment/`` config dir:

  - Auggie CLI: the standalone ``@augmentcode/auggie`` agentic terminal tool.
  - Augment (VS Code): the ``augment.vscode-augment`` marketplace extension.
  - Augment (<IDE>): the JetBrains plugin (any plugin name containing "augment").

Each surface is emitted as its own detection row (mirroring
``MacOSCopilotDetector``), and the discovery loop flattens the returned list. The
shared ``~/.augment`` config (MCP / rules / skills / permissions) is attached to a
single canonical surface downstream so it is not duplicated across rows.
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

from ...coding_tool_base import BaseToolDetector
from ...constants import VERSION_TIMEOUT
from ...macos.jetbrains.jetbrains import MacOSJetBrainsDetector
from ...macos_extraction_helpers import is_running_as_root
from ...utils import run_command

logger = logging.getLogger(__name__)

_AUGMENT_DIR_NAME = ".augment"
# VS Code marketplace extension ids (stable + nightly).
_VSCODE_EXTENSION_IDS = ("augment.vscode-augment", "augment.vscode-augment-nightly")
# Substring matched (case-insensitive) against a JetBrains plugin name.
_JETBRAINS_PLUGIN_MATCH = "augment"

_VERSION_RE = re.compile(r"\d+\.\d+\.\d+(?:[.\-+][0-9A-Za-z.\-]+)?")


def _resolve_augment_dir(user_home: Path) -> Path:
    """Return the Augment config directory for ``user_home`` (``~/.augment``).

    Augment has no documented config-dir override env var, so this is always
    ``<user_home>/.augment`` (verified: no AUGMENT_HOME/AUGGIE_HOME exists).
    """
    return user_home / _AUGMENT_DIR_NAME


def _resolve_auggie_binary(user_home: Path) -> Optional[Path]:
    """Return the per-user ``auggie`` CLI binary for ``user_home``, if found.

    The CLI installs to a per-user location that root's PATH does not include
    during an MDM all-users scan, so we resolve the binary explicitly from the
    documented/observed install locations, in order:

      - ``~/.local/bin/auggie`` (npm/standalone user install)
      - ``~/.bun/bin/auggie`` (Bun global install)
      - ``~/.nvm/versions/node/*/bin/auggie`` (nvm-managed Node; newest first)

    Best-effort only: any error is swallowed and None is returned. Never raises.
    """
    def _node_version_key(version_dir: Path):
        nums = re.findall(r"\d+", version_dir.name)
        return tuple(int(n) for n in nums)

    try:
        for candidate in (
            user_home / ".local" / "bin" / "auggie",
            user_home / ".bun" / "bin" / "auggie",
        ):
            try:
                if candidate.exists() and os.access(str(candidate), os.X_OK):
                    return candidate
            except OSError:
                continue
        nvm_node_dir = user_home / ".nvm" / "versions" / "node"
        try:
            for version_dir in sorted(nvm_node_dir.glob("*"), key=_node_version_key, reverse=True):
                try:
                    candidate = version_dir / "bin" / "auggie"
                    if candidate.exists() and os.access(str(candidate), os.X_OK):
                        return candidate
                except OSError:
                    continue
        except OSError:
            pass
    except (PermissionError, OSError) as exc:
        logger.debug(f"Error resolving Auggie CLI binary for {user_home}: {exc}")
    return None


def _parse_cli_version(raw: Optional[str]) -> Optional[str]:
    """Extract a clean semver from raw ``auggie --version`` output.

    E.g. ``"0.30.0 (commit 690bba03)"`` -> ``"0.30.0"``. Falls back to the first
    non-empty line (capped) when no semver is present; None/garbage -> None.
    """
    if not raw:
        return None
    match = _VERSION_RE.search(raw)
    if match:
        return match.group(0)
    first_line = next((line.strip() for line in raw.splitlines() if line.strip()), "")
    return first_line[:50] or None


def _load_extension_json(path: Path) -> List[Dict]:
    """Parse a VS Code ``extensions.json`` file; ``[]`` on any failure."""
    try:
        if not path.is_file():
            return []
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError, ValueError):
        return []


class MacOSAugmentDetector(BaseToolDetector):
    """
    Detects Augment Code across the Auggie CLI, VS Code, and JetBrains on macOS.

    When ``self.user_home`` is set (the live per-user discovery path), detection is
    scoped to that single user; otherwise, when running as root, all users under
    ``/Users`` are scanned, and for a regular user only their own home is checked.
    Each surface yields its own row whose ``_config_path`` is that user's resolved
    ``~/.augment`` dir.
    """

    def __init__(self) -> None:
        self.user_home: Optional[Path] = None

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "Augment Code"

    def detect(self) -> Optional[List[Dict]]:
        """
        Detect all Augment Code surfaces on macOS.

        Returns the concatenated CLI + VS Code + JetBrains rows, or None when none
        are found. The discovery loop flattens the returned list.
        """
        results: List[Dict] = []
        results.extend(self._detect_auggie_cli_all_users())
        results.extend(self._detect_vscode_all_users())
        results.extend(self._detect_jetbrains_all_users())
        return results or None

    def detect_all_tools(self, user_home: Optional[str] = None) -> List[Dict]:
        """Entry point mirroring other multi-result detectors."""
        if user_home is not None:
            self.user_home = Path(user_home)
        return self.detect() or []

    def get_version(self, binary: Optional[str] = None) -> Optional[str]:
        """Extract the Auggie CLI version via ``auggie --version`` (best-effort)."""
        if binary is not None:
            try:
                return _parse_cli_version(
                    run_command([str(binary), "--version"], VERSION_TIMEOUT)
                )
            except Exception as exc:
                logger.debug(f"Could not extract Auggie CLI version from resolved binary: {exc}")
                return None
        try:
            return _parse_cli_version(run_command(["auggie", "--version"], VERSION_TIMEOUT))
        except Exception as exc:
            logger.debug(f"Could not extract Auggie CLI version: {exc}")
        return None

    # -- per-surface, all-users helpers --------------------------------------

    def _iter_scan_homes(self) -> List[Path]:
        """User homes to scan: this user (scoped), else /Users (root), else home."""
        if self.user_home is not None:
            return [self.user_home]
        if is_running_as_root():
            homes: List[Path] = []
            users_dir = Path("/Users")
            try:
                if users_dir.exists():
                    for user_dir in users_dir.iterdir():
                        if user_dir.is_dir() and not user_dir.name.startswith("."):
                            homes.append(user_dir)
            except (PermissionError, OSError) as exc:
                logger.debug(f"Error scanning /Users for Augment: {exc}")
            return homes
        return [Path.home()]

    def _detect_auggie_cli_all_users(self) -> List[Dict]:
        results: List[Dict] = []
        for user_home in self._iter_scan_homes():
            try:
                row = self._detect_auggie_cli_for_user(user_home)
                if row:
                    results.append(row)
            except (PermissionError, OSError) as exc:
                logger.debug(f"Skipping Auggie CLI for {user_home}: {exc}")
        return results

    def _detect_auggie_cli_for_user(self, user_home: Path) -> Optional[Dict]:
        """Gate the Auggie CLI row on the resolved ``auggie`` binary."""
        binary = self._resolve_binary(user_home)
        if not binary:
            return None
        return {
            "name": "Auggie CLI",
            "version": self.get_version(binary) or "unknown",
            "publisher": "Augment Computer",
            "install_path": binary,
            "_config_path": str(_resolve_augment_dir(user_home)),
        }

    def _resolve_binary(self, user_home: Path) -> Optional[str]:
        """Resolve the ``auggie`` CLI binary for ``user_home`` (the CLI gate)."""
        per_user = _resolve_auggie_binary(user_home)
        return str(per_user) if per_user is not None else None

    def _detect_vscode_all_users(self) -> List[Dict]:
        results: List[Dict] = []
        for user_home in self._iter_scan_homes():
            try:
                results.extend(self._detect_vscode_for_user(user_home))
            except (PermissionError, OSError) as exc:
                logger.debug(f"Skipping Augment VS Code for {user_home}: {exc}")
        return results

    def _detect_vscode_for_user(self, user_home: Path) -> List[Dict]:
        """Emit AT MOST ONE "Augment (VS Code)" row per user.

        Both the stable (``augment.vscode-augment``) and nightly
        (``augment.vscode-augment-nightly``) extensions can be installed at once.
        Emitting both produces two identically-named rows sharing one
        ``_config_path`` -> duplicate canonical candidates. Prefer the stable
        extension; fall back to nightly only when stable is absent, using the
        chosen extension's version.
        """
        vscode_ext_path = user_home / ".vscode" / "extensions" / "extensions.json"
        versions_by_id: Dict[str, str] = {}
        for ext in _load_extension_json(vscode_ext_path):
            ext_id = ext.get("identifier", {}).get("id", "").lower()
            if ext_id in _VSCODE_EXTENSION_IDS:
                # _VSCODE_EXTENSION_IDS is ordered (stable, nightly); index 0 is
                # the stable id we prefer.
                versions_by_id[ext_id] = ext.get("version", "unknown")

        chosen_version = next(
            (versions_by_id[ext_id] for ext_id in _VSCODE_EXTENSION_IDS
             if ext_id in versions_by_id),
            None,
        )
        if chosen_version is None:
            return []
        return [{
            "name": "Augment (VS Code)",
            "version": chosen_version,
            "publisher": "Augment Computer",
            "install_path": str(vscode_ext_path.parent),
            "_config_path": str(_resolve_augment_dir(user_home)),
        }]

    def _detect_jetbrains_all_users(self) -> List[Dict]:
        """Detect Augment's JetBrains plugin, attributing each IDE to its OWNER.

        ``MacOSJetBrainsDetector`` already scans the running user's home (and ALL
        users under root), so we invoke it ONCE and derive each IDE's owning user
        from the IDE's own config path. Stamping the outer scan home instead would,
        under a root all-users scan, attribute one user's IDE to another user's
        ``~/.augment`` (wrong permissions/config) and re-run the scan N times.
        """
        candidate_homes = self._iter_scan_homes()
        try:
            ides = self._make_jetbrains_detector().detect() or []
        except (PermissionError, OSError) as exc:
            logger.debug(f"Skipping Augment JetBrains detection: {exc}")
            return []

        results: List[Dict] = []
        for ide in ides:
            plugins = ide.get("plugins", [])
            if not any(_JETBRAINS_PLUGIN_MATCH in str(name).lower() for name in plugins):
                continue
            ide_path = ide.get("config_path") or ide.get("install_path")
            owner_home = self._augment_owner_home_for_path(ide_path, candidate_homes)
            results.append({
                "name": f"Augment ({ide['name']})",
                "version": ide.get("version", "unknown"),
                "publisher": "Augment Computer",
                "ide": ide["name"],
                "install_path": ide_path,
                "_config_path": str(_resolve_augment_dir(owner_home)),
            })
        return results

    def _augment_owner_home_for_path(self, ide_path, candidate_homes: List[Path]) -> Path:
        """Owning user's home for a JetBrains IDE config path.

        Matches the IDE's own path against the scanned user homes (longest prefix
        wins) so each row's ``_config_path`` points at the IDE owner's
        ``~/.augment`` — correct under a root all-users scan where the JetBrains
        detector returns every user's IDEs. Falls back to the scoped/current home
        when no scanned home is a prefix. Separator-normalised for Windows.
        """
        if ide_path:
            ide_norm = str(ide_path).replace("\\", "/").rstrip("/")
            best = None
            best_len = -1
            for home in candidate_homes:
                home_norm = str(home).replace("\\", "/").rstrip("/")
                if home_norm and (ide_norm == home_norm or ide_norm.startswith(home_norm + "/")):
                    if len(home_norm) > best_len:
                        best, best_len = home, len(home_norm)
            if best is not None:
                return best
        return self.user_home or Path.home()

    def _make_jetbrains_detector(self):
        """OS seam: the JetBrains detector for this platform."""
        return MacOSJetBrainsDetector()
