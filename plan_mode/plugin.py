"""Enforced plan mode for Hermes tool dispatch.

The one sanctioned Hermes-internal dependency is imported lazily in
``_session_reader``.  Upstream binds ``gateway.session_context.get_session_env``
around plugin command handlers in ``gateway/run_inbound.py:1062-1072`` and
propagates that ContextVar state to tool workers.  Keeping the import lazy lets
the plugin load on older Hermes versions and refuse activation cleanly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import threading
from typing import Any, Callable


READ_ONLY_TOOLS = frozenset(
    {
        "browser_get_images",
        "browser_snapshot",
        "browser_vision",
        "clarify",
        "read_file",
        "read_terminal",
        "read_window_below",
        "search_files",
        "session_search",
        "skill_view",
        "skills_list",
        "todo_list",
        "video_analyze",
        "vision_analyze",
        "web_extract",
        "web_search",
    }
)
PLAN_WRITERS = frozenset({"write_file", "patch"})
_ACTIVE_INDEX_KEY = "active-index"

_V4A_FILE_RE = re.compile(
    r"^\*\*\*\s*(?:Update|Add|Delete)\s+File:\s*(.+)$", re.MULTILINE
)
_V4A_MOVE_RE = re.compile(
    r"^\*\*\*\s*Move\s+File:\s*(.+?)\s*->\s*(.+)$", re.MULTILINE
)


@dataclass(frozen=True)
class SessionIdentity:
    """A stable plugin state key or a reason it cannot be derived."""

    key: str | None
    unsupported: bool = False
    non_cli_without_key: bool = False
    surface: str = ""


def _session_reader() -> Callable[[str, str], str] | None:
    """Return the addendum-sanctioned identity reader when Hermes provides it."""
    try:
        # Sanctioned by WS2 Addendum 1.  This exact seam is bound around gateway
        # plugin handlers at gateway/run_inbound.py:1062-1072 (#108698).
        from gateway.session_context import get_session_env
    except Exception:
        return None
    return get_session_env


def derive_session_identity(platform_hint: str = "") -> SessionIdentity:
    """Derive one key identically for commands and all registered hooks."""
    reader = _session_reader()
    if reader is None:
        return SessionIdentity(None, unsupported=True)

    session_key = str(reader("HERMES_SESSION_KEY", "") or "").strip()
    if session_key:
        return SessionIdentity(f"sk:{session_key}")

    surface = (
        str(reader("HERMES_SESSION_PLATFORM", "") or "").strip()
        or str(reader("HERMES_SESSION_SOURCE", "") or "").strip()
        or str(platform_hint or "").strip()
    )
    if surface and surface.lower() not in {"cli", "terminal"}:
        return SessionIdentity(None, non_cli_without_key=True, surface=surface)
    return SessionIdentity(f"cli:{os.getpid()}", surface=surface or "cli")


def _state_storage_key(session_key: str) -> str:
    digest = hashlib.sha256(session_key.encode("utf-8")).hexdigest()
    return f"session:{digest}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _block_message(tool_name: str, detail: str = "") -> dict[str, str]:
    base = (
        f"Plan mode is on: {tool_name} is blocked until you approve the plan "
        "(/planmode approve). You may write the plan under .hermes/plans/."
    )
    return {"action": "block", "message": f"{base} {detail}".strip()}


def _path_is_inside(target: str, plans_dir: str) -> bool:
    """Return true only for an absolute target resolving below ``plans_dir``."""
    if not isinstance(target, str) or not target.strip() or not os.path.isabs(target):
        return False
    if ".." in Path(target).parts:
        return False
    try:
        resolved_target = os.path.realpath(target)
        resolved_plans = os.path.realpath(plans_dir)
        return (
            resolved_target != resolved_plans
            and os.path.commonpath((resolved_target, resolved_plans)) == resolved_plans
        )
    except (OSError, ValueError):
        return False


def _patch_targets(args: dict[str, Any]) -> list[str] | None:
    """Extract every replace/V4A patch target; ``None`` means not explicit/safe."""
    mode = str(args.get("mode") or "replace").strip().lower()
    if mode == "replace":
        path = args.get("path")
        return [path] if isinstance(path, str) and path.strip() else None
    if mode != "patch" or not isinstance(args.get("patch"), str):
        return None

    patch = args["patch"]
    targets = [match.group(1).strip() for match in _V4A_FILE_RE.finditer(patch)]
    for match in _V4A_MOVE_RE.finditer(patch):
        targets.extend((match.group(1).strip(), match.group(2).strip()))
    return targets or None


def _write_targets(tool_name: str, args: dict[str, Any]) -> list[str] | None:
    if tool_name == "write_file":
        path = args.get("path")
        return [path] if isinstance(path, str) and path.strip() else None
    if tool_name == "patch":
        return _patch_targets(args)
    return None


class PlanModePlugin:
    """State machine and dispatch guard registered by the plugin."""

    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self._lock = threading.RLock()
        self._active_keys: set[str] = set()

    def register(self) -> None:
        self.ctx.register_command(
            "planmode",
            self.command,
            "Enforce plan-only tool access for this session",
            args_hint="on|status|approve|reject|off [task]",
        )
        self.ctx.register_hook("pre_tool_call", self.pre_tool_call)
        self.ctx.register_hook("pre_llm_call", self.pre_llm_call)
        self.ctx.register_hook("on_session_reset", self.on_session_reset)

    def _load_state(self, key: str) -> dict[str, Any]:
        value = self.ctx.state.get(_state_storage_key(key), {})
        return dict(value) if isinstance(value, dict) else {}

    def _active_storage_keys(self) -> list[str]:
        value = self.ctx.state.get(_ACTIVE_INDEX_KEY, [])
        if not isinstance(value, list):
            return []
        return [item for item in value[:256] if isinstance(item, str) and item.startswith("session:")]

    def _set_active_storage_keys(self, values: list[str]) -> None:
        self.ctx.state.set(_ACTIVE_INDEX_KEY, sorted(set(values))[:256])

    def _clear_storage_key(self, storage_key: str) -> None:
        self.ctx.state.set(storage_key, {})
        self._set_active_storage_keys(
            [item for item in self._active_storage_keys() if item != storage_key]
        )
        self._active_keys = {
            key for key in self._active_keys if _state_storage_key(key) != storage_key
        }

    def _save_state(self, key: str, state: dict[str, Any]) -> None:
        state = dict(state)
        storage_key = _state_storage_key(key)
        self.ctx.state.set(storage_key, state)
        active_storage = self._active_storage_keys()
        if state.get("active"):
            self._active_keys.add(key)
            if storage_key not in active_storage:
                active_storage.append(storage_key)
        else:
            self._active_keys.discard(key)
            active_storage = [item for item in active_storage if item != storage_key]
        self._set_active_storage_keys(active_storage)

    @staticmethod
    def _session_id_hash(value: Any) -> str:
        text = str(value or "").strip()
        return hashlib.sha256(text.encode("utf-8")).hexdigest() if text else ""

    def _remember_session_id(self, key: str, state: dict[str, Any], value: Any) -> None:
        digest = self._session_id_hash(value)
        if digest and state.get("session_id_hash") != digest:
            state["session_id_hash"] = digest
            self._save_state(key, state)

    def _identity_or_reply(self) -> tuple[SessionIdentity, str | None]:
        identity = derive_session_identity()
        if identity.unsupported:
            return identity, (
                "Plan mode is unavailable: this Hermes version does not provide the "
                "required session identity seam (gateway.session_context.get_session_env)."
            )
        if identity.non_cli_without_key or not identity.key:
            return identity, (
                "Plan mode cannot identify this non-CLI session safely. Upgrade Hermes or "
                "use a surface that binds HERMES_SESSION_KEY."
            )
        return identity, None

    def _fixed_plans_dir(self) -> str:
        reader = _session_reader()
        raw = str(reader("TERMINAL_CWD", "") if reader else "").strip()
        if not raw or not os.path.isabs(raw) or not os.path.isdir(raw):
            raw = os.getcwd()
        base = os.path.realpath(raw)
        plan_path = os.path.join(base, ".hermes", "plans")
        os.makedirs(plan_path, exist_ok=True)
        return os.path.realpath(plan_path)

    @staticmethod
    def _plan_files(state: dict[str, Any]) -> list[str]:
        plans_dir = state.get("plans_dir")
        if not isinstance(plans_dir, str) or not os.path.isdir(plans_dir):
            return []
        try:
            return sorted(
                str(path.resolve())
                for path in Path(plans_dir).glob("*.md")
                if path.is_file()
            )
        except OSError:
            return []

    def command(self, raw_args: str) -> str:
        identity, error = self._identity_or_reply()
        if error:
            return error
        assert identity.key is not None

        raw = str(raw_args or "").strip()
        action, _, remainder = raw.partition(" ")
        action = action.lower() or "status"
        remainder = remainder.strip()

        with self._lock:
            state = self._load_state(identity.key)
            if action == "on":
                plans_dir = self._fixed_plans_dir()
                state = {
                    "active": True,
                    "entered_at": _utc_now(),
                    "plans_dir": plans_dir,
                    "task": remainder,
                    "pending_note": "",
                }
                self._save_state(identity.key, state)
                task_text = f" Task: {remainder}" if remainder else ""
                return (
                    f"Plan mode is on for this session.{task_text}\n"
                    f"Write plans only to absolute paths under {plans_dir}.\n"
                    "Use YYYY-MM-DD_HHMMSS-<slug>.md. Read-only planning tools are allowed; "
                    "terminal, code execution, delegation, connectors/MCP, messaging, browser "
                    "mutations, and unknown tools are blocked until /planmode approve."
                )

            if action == "status":
                files = self._plan_files(state)
                mode = "on" if state.get("active") else "off"
                entered = state.get("entered_at") or "not set"
                plans_dir = state.get("plans_dir") or "not set"
                rendered = "\n".join(f"- {path}" for path in files) or "- none"
                return (
                    f"Plan mode: {mode}\nEntered at: {entered}\n"
                    f"Plans directory: {plans_dir}\nPlan files:\n{rendered}"
                )

            if action == "approve":
                if not state.get("active"):
                    return "Plan mode is not on for this session."
                files = self._plan_files(state)
                approved_path = files[-1] if files else str(state.get("plans_dir") or "the plans directory")
                state["active"] = False
                state["pending_note"] = (
                    f"The user approved the plan at {approved_path}. Implement it now."
                )
                self._save_state(identity.key, state)
                return f"Plan approved. Plan mode is off. Next turn will implement {approved_path}."

            if action == "reject":
                if not state.get("active"):
                    return "Plan mode is not on for this session."
                feedback = remainder or "No additional feedback was provided."
                state["pending_note"] = f"The user rejected the plan: {feedback}. Revise it."
                self._save_state(identity.key, state)
                return "Plan rejected. Plan mode remains on; the feedback will be injected next turn."

            if action == "off":
                state["active"] = False
                state["pending_note"] = ""
                self._save_state(identity.key, state)
                return "Plan mode is off for this session. No approval note will be injected."

        return "Usage: /planmode on [task] | status | approve | reject [feedback] | off"

    def _extra_allowed_tools(self) -> set[str]:
        value = self.ctx.get_config("plan_mode.extra_allowed_tools", None)
        if value is None:
            value = self.ctx.get_config("extra_allowed_tools", [])
        if not isinstance(value, list):
            return set()
        return {item.strip() for item in value if isinstance(item, str) and item.strip()}

    def pre_tool_call(
        self, tool_name: str = "", args: Any = None, **kwargs: Any
    ) -> dict[str, str] | None:
        """Enforce the plan-mode allowlist, failing closed on internal errors."""
        try:
            identity = derive_session_identity(str(kwargs.get("platform") or ""))
            if identity.unsupported:
                return None
            if identity.non_cli_without_key or not identity.key:
                if self._active_keys:
                    return _block_message(
                        tool_name or "unknown tool",
                        "The active session key could not be derived, so this request was blocked fail-closed.",
                    )
                return None

            with self._lock:
                state = self._load_state(identity.key)
                if not state.get("active"):
                    self._active_keys.discard(identity.key)
                    return None
                self._active_keys.add(identity.key)
                self._remember_session_id(identity.key, state, kwargs.get("session_id"))

                name = str(tool_name or "")
                call_args = args if isinstance(args, dict) else {}
                if name in READ_ONLY_TOOLS or name in self._extra_allowed_tools():
                    return None
                if name in PLAN_WRITERS:
                    targets = _write_targets(name, call_args)
                    plans_dir = state.get("plans_dir")
                    if not targets or not isinstance(plans_dir, str):
                        return _block_message(name, "Every write target must be explicit and absolute.")
                    if all(_path_is_inside(target, plans_dir) for target in targets):
                        return None
                    return _block_message(
                        name,
                        f"Use only absolute paths resolving inside {plans_dir}; relative, outside, "
                        "traversal, and symlink-escaping targets are refused.",
                    )
                return _block_message(name or "unknown tool")
        except Exception as exc:
            return _block_message(
                str(tool_name or "unknown tool"),
                f"The plan-mode safety check failed closed ({type(exc).__name__}).",
            )

    def pre_llm_call(self, **kwargs: Any) -> dict[str, str] | None:
        identity = derive_session_identity(str(kwargs.get("platform") or ""))
        if identity.unsupported:
            return None
        if identity.non_cli_without_key or not identity.key:
            if self._active_keys:
                return {
                    "context": (
                        "Plan mode is active in this process, but this turn's session key could not "
                        "be derived. Tool calls will be blocked fail-closed."
                    )
                }
            return None

        try:
            with self._lock:
                state = self._load_state(identity.key)
                parts: list[str] = []
                pending = state.get("pending_note")
                if isinstance(pending, str) and pending.strip():
                    parts.append(pending.strip())
                    state["pending_note"] = ""
                    self._save_state(identity.key, state)
                if state.get("active"):
                    self._active_keys.add(identity.key)
                    self._remember_session_id(identity.key, state, kwargs.get("session_id"))
                    plans_dir = state.get("plans_dir")
                    parts.append(
                        "Plan mode is ON. Explore with the allowed read-only tools and write only "
                        f"plan Markdown files using absolute paths under {plans_dir}. Name each plan "
                        "YYYY-MM-DD_HHMMSS-<slug>.md. Do not implement or call blocked tools; ask "
                        "the user to approve with /planmode approve when the plan is ready."
                    )
                return {"context": "\n\n".join(parts)} if parts else None
        except Exception as exc:
            return {
                "context": (
                    "Plan-mode state could not be read safely "
                    f"({type(exc).__name__}); tool calls will fail closed."
                )
            }

    def on_session_reset(self, **kwargs: Any) -> None:
        identity = derive_session_identity(str(kwargs.get("platform") or ""))
        if identity.unsupported:
            return
        with self._lock:
            if identity.key and not identity.non_cli_without_key:
                self._save_state(identity.key, {})
                return

            # Gateway's reset callback may run outside the command's bound
            # ContextVar scope. Match the hashed old session id when possible;
            # otherwise clear only a unique active session. Never guess among
            # multiple sessions, because that would weaken another session.
            active_storage = self._active_storage_keys()
            old_digest = self._session_id_hash(
                kwargs.get("old_session_id") or kwargs.get("session_id")
            )
            matches = []
            if old_digest:
                for storage_key in active_storage:
                    state = self.ctx.state.get(storage_key, {})
                    if isinstance(state, dict) and state.get("session_id_hash") == old_digest:
                        matches.append(storage_key)
            if len(matches) == 1:
                self._clear_storage_key(matches[0])
            elif not matches and len(active_storage) == 1:
                self._clear_storage_key(active_storage[0])
