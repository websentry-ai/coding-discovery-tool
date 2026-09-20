"""GitHub Copilot for Xcode auto-approval permission extraction (macOS only).

Copilot for Xcode is a sandboxed app: its agent auto-approval model does not live
in a JSON settings file like the VS Code extension's does, but in per-user
UserDefaults *suites* written under the app-group container:

    ~/Library/Group Containers/VEKTX9H2N7.group.com.github.CopilotForXcode/Library/Preferences/

Two suites carry the permission surface:

  * ``<group>.autoApproval.prefs`` — the auto-approval model itself:
      - ``AutoApproval_MCP_GlobalApprovals``          auto-approved MCP servers
      - ``AutoApproval_Terminal_GlobalApprovals``     auto-approved terminal commands
      - ``AutoApproval_SensitiveFiles_GlobalApprovals`` sensitive-file approval rules
  * ``<group>.prefs`` — the policy toggles that gate the model:
      - ``agentMode.autoApproval.enabled``
      - ``cveRemediatorAgent.enabled``

Dev builds write to a parallel ``VEKTX9H2N7.group.dev.com.github.CopilotForXcode``
group; both prod and dev are read, prod preferred. macOS occasionally writes a
suite to the non-group ``~/Library/Preferences/<suite>.plist`` instead, so that
location is used as a fallback only when the group-container file is absent.

The emitted record stays inside the Cursor/VS-Code-Copilot permission vocabulary
(``permission_mode`` / ``allow_rules`` / ``mcp_tool_allowlist`` / ``raw_settings``)
so gateway-data's AIToolPermissions ingest and the frontend accept it with no
backend change — the auto-approved MCP servers, terminal commands and
sensitive-file rules ride the same fields the sibling Copilot surfaces already use.

This runs on customer machines: every read is best-effort and never raises.
"""

import base64
import datetime
import json
import logging
import os
import plistlib
import stat
from pathlib import Path
from typing import Dict, List, Optional

from ...macos_extraction_helpers import (
    _PLIST_OPEN_FLAGS,
    is_running_as_root,
    path_in_scope,
    scan_user_directories,
)

logger = logging.getLogger(__name__)

# App-group ids. Prod is preferred; a dev build writes the parallel ``.dev.`` group.
_PROD_GROUP = "VEKTX9H2N7.group.com.github.CopilotForXcode"
_DEV_GROUP = "VEKTX9H2N7.group.dev.com.github.CopilotForXcode"

# UserDefaults suite basenames, relative to a group id (``<group>.<suffix>``).
_AUTOAPPROVAL_SUFFIX = "autoApproval.prefs"
_GENERAL_SUFFIX = "prefs"

# Keys inside the auto-approval suite (the permission model).
_MCP_KEY = "AutoApproval_MCP_GlobalApprovals"
_TERMINAL_KEY = "AutoApproval_Terminal_GlobalApprovals"
_SENSITIVE_FILES_KEY = "AutoApproval_SensitiveFiles_GlobalApprovals"

# Policy toggles inside the general suite that gate the model.
_TOGGLE_KEYS = ("agentMode.autoApproval.enabled", "cveRemediatorAgent.enabled")

# A sandboxed app's plist is small; refuse a pathological one rather than load it.
_PLIST_MAX_BYTES = 5 * 1024 * 1024


class MacOSCopilotXcodeSettingsExtractor:
    """Extract Copilot-for-Xcode auto-approval permissions on macOS.

    Mirrors the ``extract_settings`` / ``extract_settings_by_user`` interface the
    VS Code Copilot extractor exposes, so the discovery assembly attaches the
    resulting ``permissions`` block exactly as it does for the other Copilot rows.
    """

    def _scan_users(self, callback) -> None:
        """Call ``callback(user_home)`` for each home to scan: every user under an
        elevated (MDM) scan, else just the current user — the same enumeration the
        macOS Cursor/Claude/Copilot extractors use."""
        if is_running_as_root():
            scan_user_directories(callback)
        else:
            callback(Path.home())

    def extract_settings_by_user(self) -> List[Dict]:
        """One backend-ready record per user that has a Copilot-for-Xcode
        auto-approval surface, riskiest first. The per-user report filter picks
        the record whose ``settings_path`` is in that user's home, so no user's
        posture is dropped in favour of another's."""
        records: List[Dict] = []

        def extract_for_user(user_home) -> None:
            try:
                rec = self._extract_for_user(Path(user_home))
                if rec:
                    records.append(rec)
            except Exception as e:
                logger.error(
                    f"Error extracting Copilot-for-Xcode settings for {user_home}: {e}",
                    exc_info=True,
                )

        self._scan_users(extract_for_user)
        records.sort(key=self._permissiveness, reverse=True)
        return records

    def extract_settings(self) -> Optional[Dict]:
        """The riskiest user's record — the single-record view for a report not
        scoped to one user. None when no auto-approval surface is present."""
        records = self.extract_settings_by_user()
        return records[0] if records else None

    @staticmethod
    def _permissiveness(record: Dict) -> tuple:
        """Rank a record so the riskiest posture wins: an armed master toggle
        first, then more auto-approved rules and servers."""
        armed = record.get("raw_settings", {}).get("agentMode.autoApproval.enabled") is True
        return (1 if armed else 0,
                len(record.get("allow_rules", [])) + len(record.get("mcp_tool_allowlist", [])))

    # -- per-user assembly ---------------------------------------------------

    def _extract_for_user(self, user_home: Path) -> Optional[Dict]:
        """One record for this user, from the prod suites if present, else dev.
        Prod is preferred so a real install is never reported through a dev build's
        stale approvals. None when neither group holds a permission surface."""
        for group in (_PROD_GROUP, _DEV_GROUP):
            record = self._extract_for_group(user_home, group)
            if record:
                return record
        return None

    def _extract_for_group(self, user_home: Path, group: str) -> Optional[Dict]:
        auto_path = self._resolve_suite_plist(user_home, group, _AUTOAPPROVAL_SUFFIX)
        prefs_path = self._resolve_suite_plist(user_home, group, _GENERAL_SUFFIX)
        if auto_path is None and prefs_path is None:
            return None

        approvals = self._load_plist(auto_path, user_home) if auto_path else {}
        toggles = self._load_plist(prefs_path, user_home) if prefs_path else {}
        # A present-but-unreadable/empty auto-approval suite with no toggles is
        # not a permission surface worth a row.
        if not approvals and not any(k in (toggles or {}) for k in _TOGGLE_KEYS):
            return None

        return self._build_record(approvals or {}, toggles or {}, auto_path or prefs_path)

    def _resolve_suite_plist(self, user_home: Path, group: str, suffix: str) -> Optional[Path]:
        """Path to ``<group>.<suffix>.plist`` for this user: the group-container
        location first, the non-group ``~/Library/Preferences`` fallback only when
        the group-container file is absent.

        A candidate is accepted only when every path component below the scanned
        home is a real (non-symlink) file — the same ``path_in_scope`` boundary the
        Copilot-for-Xcode detector uses, so under a root/MDM scan a symlinked
        container cannot redirect the read onto another user's file. A rejected
        group path falls through to the fallback rather than poisoning it."""
        basename = f"{group}.{suffix}.plist"
        group_path = (user_home / "Library" / "Group Containers" / group
                      / "Library" / "Preferences" / basename)
        fallback = user_home / "Library" / "Preferences" / basename
        for candidate in (group_path, fallback):
            if path_in_scope(candidate, user_home) and self._is_regular_file(candidate):
                return candidate
        return None

    @staticmethod
    def _is_regular_file(path: Path) -> bool:
        try:
            return stat.S_ISREG(os.lstat(path).st_mode)
        except OSError:
            return False

    # -- record shape --------------------------------------------------------

    def _build_record(self, approvals: Dict, toggles: Dict, path: Path) -> Dict:
        """Map the two suites onto the shared Copilot/Cursor permission vocabulary.

        Terminal + sensitive-file approvals become ``allow_rules`` (Bash / Edit
        rule strings, the same mixed-verb list Claude and Cursor emit); MCP
        approvals become ``mcp_tool_allowlist``; the raw approval values and the
        two policy toggles are kept verbatim in ``raw_settings`` for audit.
        """
        allow_rules: List[str] = []
        allow_rules += [f"Bash({name} *)" for name in self._names(approvals.get(_TERMINAL_KEY))]
        allow_rules += [f"Edit({name})" for name in self._names(approvals.get(_SENSITIVE_FILES_KEY))]
        mcp_allowlist = self._names(approvals.get(_MCP_KEY))

        # Plist values can be bytes (``<data>``) or datetime (``<date>``), which
        # json.dumps cannot encode — coerced to JSON-safe forms here so the
        # report's serialization (payload hashing/delivery) never breaks.
        raw_settings: Dict = {}
        for key in (_MCP_KEY, _TERMINAL_KEY, _SENSITIVE_FILES_KEY):
            if key in approvals:
                raw_settings[key] = self._json_safe(self._coerce(approvals[key]))
        for key in _TOGGLE_KEYS:
            if key in toggles:
                raw_settings[key] = self._json_safe(toggles[key])

        record: Dict = {
            "settings_source": "user",
            "scope": "user",
            "settings_path": str(path),
            "raw_settings": raw_settings,
            # These keys never bypass every confirmation the way VS Code's global
            # auto-approve does; the specific approvals ARE the posture, expressed
            # as allow rules — so mode stays "default", matching an armed-with-rules
            # Claude/Copilot record.
            "permission_mode": "default",
            "sandbox_enabled": None,  # Copilot for Xcode exposes no sandbox toggle
        }
        if allow_rules:
            record["allow_rules"] = self._dedupe(allow_rules)
        if mcp_allowlist:
            record["mcp_tool_allowlist"] = self._dedupe(mcp_allowlist)
        return record

    # -- defensive value parsing --------------------------------------------

    @staticmethod
    def _coerce(value):
        """A UserDefaults value that may be a JSON-encoded string. Decode it when
        it parses, else return it unchanged."""
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (ValueError, TypeError):
                return value
        return value

    @classmethod
    def _json_safe(cls, value):
        """Recursively coerce a plist value into a JSON-serializable form: bytes
        (``<data>``) to a base64 string, datetime (``<date>``) to ISO-8601, and
        anything else unrecognized to ``str``. Containers are walked so a nested
        ``<data>``/``<date>`` cannot slip through and break ``json.dumps``."""
        if isinstance(value, (str, bool, int, float)) or value is None:
            return value
        if isinstance(value, (bytes, bytearray)):
            return base64.b64encode(bytes(value)).decode("ascii")
        if isinstance(value, datetime.datetime):
            return value.isoformat()
        if isinstance(value, dict):
            return {str(k): cls._json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._json_safe(v) for v in value]
        return str(value)

    @classmethod
    def _names(cls, value) -> List[str]:
        """Best-effort list of approval identifiers from any of the shapes these
        values take: a JSON string, a list of names, a list of ``{"name": ...}``
        entries, or a dict keyed by name (only truthy / ``approve``-true entries)."""
        value = cls._coerce(value)
        out: List[str] = []
        if isinstance(value, list):
            for item in value:
                # A list entry can carry its own verdict (``{"name": x,
                # "approve": false}``); apply the SAME filter as the dict-keyed
                # path so a withheld approval is never reported as active.
                if isinstance(item, dict) and not cls._is_approved(item):
                    continue
                name = cls._name_of(item)
                if name:
                    out.append(name)
        elif isinstance(value, dict):
            for key, verdict in value.items():
                if isinstance(key, str) and key and cls._is_approved(verdict):
                    out.append(key)
        return out

    @staticmethod
    def _name_of(item) -> Optional[str]:
        if isinstance(item, str) and item:
            return item
        if isinstance(item, dict):
            for field in ("name", "server", "command", "path", "id"):
                val = item.get(field)
                if isinstance(val, str) and val:
                    return val
        return None

    @staticmethod
    def _is_approved(verdict) -> bool:
        """A dict-keyed approval counts unless it is explicitly disabled: a bare
        ``True``, a truthy scalar, or an object whose ``approve``/``enabled`` is not
        ``False``. A bare ``False`` is a withheld approval and is skipped."""
        if isinstance(verdict, bool):
            return verdict
        if isinstance(verdict, dict):
            for field in ("approve", "enabled", "approved"):
                if field in verdict:
                    return verdict[field] is not False
            return True
        return bool(verdict)

    @staticmethod
    def _dedupe(items: List[str]) -> List[str]:
        seen, out = set(), []
        for item in items:
            if item not in seen:
                seen.add(item)
                out.append(item)
        return out

    def _load_plist(self, path: Path, user_home: Path) -> Optional[Dict]:
        """Parse a plist (binary or XML) into a dict, best-effort. None on any
        failure, on a non-dict root, or on an oversized/irregular file. Never
        raises — a planted or corrupt plist must not break the scan.

        Read through the same safe boundary ``_read_contained`` applies: refuse a
        path that escapes the scanned home (``path_in_scope``), open O_NOFOLLOW via
        the shared ``_PLIST_OPEN_FLAGS``, then judge the descriptor itself — regular
        file, size cap, single hard link, and owned by the home's user — rather than
        a re-resolved path. A hard link to another user's plist is a regular file
        that clears containment, and a differently-owned file inside the home is
        still not this user's, so under a root/MDM all-users scan both would
        otherwise be read and misattributed. The ``finally`` closes ``fd`` on every
        refuse path."""
        if not path_in_scope(path, user_home):
            logger.info(f"Refusing {path}: escapes {user_home}'s scope")
            return None
        fd = None
        try:
            # O_NOFOLLOW: a symlink AT the final component raises here instead of
            # being followed; O_NONBLOCK (from the shared flags) keeps a planted
            # FIFO from blocking the scan.
            fd = os.open(str(path), _PLIST_OPEN_FLAGS)
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                return None
            if st.st_size > _PLIST_MAX_BYTES:
                logger.info(f"Refusing {path}: exceeds the plist read cap")
                return None
            # A hard link keeps its target's owner while its path stays inside the
            # home, so containment alone cannot see through one; st_nlink catches it.
            if st.st_nlink > 1:
                logger.info(f"Refusing {path}: multiply-linked (nlink={st.st_nlink})")
                return None
            # A file owned by a different uid is not this user's, even in their tree.
            if st.st_uid != os.stat(user_home).st_uid:
                logger.info(f"Refusing {path}: owned by uid {st.st_uid}, home owner differs")
                return None
            with os.fdopen(fd, "rb") as fh:
                fd = None
                data = plistlib.load(fh)
            return data if isinstance(data, dict) else None
        except Exception as e:
            logger.debug(f"Could not parse Copilot-for-Xcode plist {path}: {e}", exc_info=True)
            return None
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
