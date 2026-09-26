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
import json
import os
from pathlib import Path
import re
import sys
import threading
from typing import Any, Callable
import uuid


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
        "skills_list",
        "todo_list",
        "video_analyze",
        "vision_analyze",
        "web_extract",
        "web_search",
    }
)
PLAN_WRITERS = frozenset({"write_file", "patch"})
PLAN_MODE_TOOL = "plan_mode"
_TOOL_DESCRIPTION = (
    "Enter plan mode for this session when the user asks you to write or draft a plan "
    "before doing the work: while it is on, file writes are allowed only under the "
    "session's plans directory and every other mutating tool is blocked. action='status' "
    "reports the state. action='off' ends ONLY a plan mode you entered yourself; a plan "
    "mode the user entered ends only with /planmode approve|reject|off."
)
_TOOL_SCHEMA = {
    "name": PLAN_MODE_TOOL,
    "description": _TOOL_DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["on", "status", "off"]},
            "reason": {"type": "string", "description": "What the plan is for."},
        },
        "required": ["action"],
    },
}
_AGENT_NOTE = (
    "You entered plan mode yourself: when the plan is written, call "
    "plan_mode(action='off') or tell the user to run /planmode approve to execute it."
)
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
    fallback_key: str | None = None
    inherited_env: bool = False


def _session_reader() -> Callable[[str, str], str] | None:
    """Return the addendum-sanctioned identity reader when Hermes provides it."""
    try:
        # Sanctioned by WS2 Addendum 1.  This exact seam is bound around gateway
        # plugin handlers at gateway/run_inbound.py:1062-1072 (#108698).
        from gateway.session_context import get_session_env
    except Exception:
        return None
    return get_session_env


def _session_context_is_engaged() -> bool:
    """Return whether Hermes has ever bound server session context in this process."""
    try:
        # Sanctioned alongside get_session_env by WS2 FIXROUND-2 N1/F7.  Unlike
        # imported-module heuristics this distinguishes a CLI importing gateway.run
        # from a server process that has actually entered a bound session path.
        from gateway.session_context import session_context_engaged
    except Exception:
        return False
    try:
        return bool(session_context_engaged())
    except Exception:
        return False


def _cron_session_is_active() -> bool:
    """Return whether Hermes marked this ContextVar-scoped turn as cron."""
    reader = _session_reader()
    if reader is None:
        return False
    return str(reader("HERMES_CRON_SESSION", "") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _gateway_process_is_admitted() -> bool:
    """Return whether Hermes admitted this process as a gateway runtime."""
    truthy = {"1", "true", "yes", "on"}
    if str(os.environ.get("HERMES_GATEWAY_SESSION") or "").strip().lower() in truthy:
        return True
    gateway_run = sys.modules.get("gateway.run")
    runner_ref = getattr(gateway_run, "_gateway_runner_ref", None)
    if not callable(runner_ref):
        return False
    try:
        return runner_ref() is not None
    except Exception:
        return False


def _skill_view_block_reason() -> str | None:
    """Return why ``skill_view`` must stay blocked, or ``None`` when it is safe.

    ``skill_view`` preprocesses SKILL.md through
    ``agent.skill_preprocessing.preprocess_skill_content`` without an explicit
    ``skills_cfg`` (``tools/skills_tool_plugin.py:117``), so the tool reads the
    same ``load_skills_config()`` this hook calls in the same process and
    context.  Inline shell runs only when that value is truthy.  Anything the
    plugin cannot read through Hermes' own loader fails closed.
    """
    try:
        from agent.skill_preprocessing import load_skills_config
    except Exception:
        return (
            "Hermes' skill config loader (agent.skill_preprocessing.load_skills_config) "
            "is unavailable, so inline shell cannot be ruled out."
        )
    try:
        skills_cfg = load_skills_config()
    except Exception as exc:
        return f"Reading skills.inline_shell failed ({type(exc).__name__})."
    if not isinstance(skills_cfg, dict):
        return "Hermes returned an unreadable skills config, so inline shell cannot be ruled out."
    if skills_cfg.get("inline_shell", False):
        return "skills.inline_shell is enabled, so viewing a skill can run shell snippets."
    return None


def _runtime_cwd_reader() -> Callable[[], Path] | None:
    """Return Hermes' turn-scoped cwd resolver required by FIXROUND-1 W3."""
    try:
        # W3 explicitly requires the cwd bound by _set_session_context(..., cwd=...).
        # Import lazily so unsupported Hermes versions refuse/fallback cleanly.
        from agent.runtime_cwd import resolve_agent_cwd
    except Exception:
        return None
    return resolve_agent_cwd


def derive_session_identity(platform_hint: str = "") -> SessionIdentity:
    """Derive one key identically for commands and all registered hooks."""
    reader = _session_reader()
    if reader is None:
        return SessionIdentity(None, unsupported=True)

    surface = (
        str(reader("HERMES_SESSION_PLATFORM", "") or "").strip()
        or str(reader("HERMES_SESSION_SOURCE", "") or "").strip()
        or str(platform_hint or "").strip()
    )
    session_key = str(reader("HERMES_SESSION_KEY", "") or "").strip()
    ui_session_id = str(reader("HERMES_UI_SESSION_ID", "") or "").strip()
    context_engaged = _session_context_is_engaged()
    inherited_cli_identity = bool(
        not context_engaged
        and (
            (
                session_key
                and session_key
                == str(os.environ.get("HERMES_SESSION_KEY") or "").strip()
            )
            or (
                ui_session_id
                and ui_session_id
                == str(os.environ.get("HERMES_UI_SESSION_ID") or "").strip()
            )
        )
    )
    if inherited_cli_identity:
        return SessionIdentity(
            f"cli:{os.getpid()}",
            surface=surface or "cli",
            inherited_env=True,
        )
    if ui_session_id:
        return SessionIdentity(
            f"ui:{ui_session_id}",
            surface=surface,
            fallback_key=f"sk:{session_key}" if session_key else None,
        )
    if session_key:
        return SessionIdentity(f"sk:{session_key}", surface=surface)

    if context_engaged or _gateway_process_is_admitted():
        return SessionIdentity(
            None,
            non_cli_without_key=True,
            surface=surface or "server",
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


def _plans_dir_is_still_safe(plans_dir: str) -> bool:
    """Revalidate the fixed plan root immediately before a writer is allowed."""
    if not isinstance(plans_dir, str) or not os.path.isabs(plans_dir):
        return False
    hermes_dir = os.path.dirname(plans_dir)
    try:
        return (
            not os.path.islink(hermes_dir)
            and not os.path.islink(plans_dir)
            and os.path.realpath(plans_dir) == plans_dir
        )
    except OSError:
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
        # (session_id, tool_call_id) -> (state key, targets, activation id) awaiting post_tool_call.
        self._pending_plan_writes: dict[tuple[str, str], tuple[str, list[str], Any]] = {}
        manager_home = getattr(getattr(ctx, "_manager", None), "home_path", None)
        self._registration_profile = self._profile_name_for_home(manager_home)

    @staticmethod
    def _profile_name_for_home(home: Any) -> str | None:
        if home is None or not str(home).strip():
            return None
        path = Path(str(home)).expanduser()
        if path.parent.name == "profiles":
            return path.name
        try:
            from hermes_cli.profiles import get_active_profile_name

            return str(get_active_profile_name() or "").strip() or None
        except Exception:
            # Unknown, not "default": a bound session then refuses every
            # state-mutating command instead of storing state no copy reads.
            return None

    def register(self) -> None:
        self.ctx.register_command(
            "planmode",
            self.command,
            "Enforce plan-only tool access for this session",
            args_hint="on|status|approve|reject|off [task]",
        )
        self.ctx.register_tool(
            name=PLAN_MODE_TOOL,
            toolset="plan-mode",
            schema=_TOOL_SCHEMA,
            handler=self.tool,
            description=_TOOL_DESCRIPTION,
        )
        self.ctx.register_hook("pre_tool_call", self.pre_tool_call)
        self.ctx.register_hook("post_tool_call", self.post_tool_call)
        self.ctx.register_hook("pre_llm_call", self.pre_llm_call)
        self.ctx.register_hook("on_session_finalize", self.on_session_finalize)
        self.ctx.register_hook("on_session_reset", self.on_session_reset)

    def _load_state(self, key: str) -> dict[str, Any]:
        return self._load_storage_state(_state_storage_key(key))

    def _load_storage_state(self, storage_key: str) -> dict[str, Any]:
        value = self.ctx.state.get(storage_key, {})
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

    def _clear_state_family(
        self, storage_key: str, state: dict[str, Any] | None = None
    ) -> None:
        state = state or self._load_storage_state(storage_key)
        related = {storage_key, *self._linked_command_storage_keys(state)}
        canonical = state.get("canonical_ui_storage_key")
        if isinstance(canonical, str) and canonical.startswith("session:"):
            related.add(canonical)
        for candidate in related:
            self._clear_storage_key(candidate)

    @staticmethod
    def _pid_is_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        if pid == os.getpid():
            return True
        if os.name == "nt":
            # os.kill(pid, 0) is not a harmless probe on Windows.  Keep the
            # bounded durable entry rather than risk signaling the process.
            return True
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    def _prune_dead_cli_states(self) -> None:
        for storage_key in list(self._active_storage_keys()):
            state = self.ctx.state.get(storage_key, {})
            pid = state.get("cli_pid") if isinstance(state, dict) else None
            if isinstance(pid, int) and not self._pid_is_alive(pid):
                self._clear_storage_key(storage_key)

    def _current_process_has_active_state(self) -> bool:
        current_pid = os.getpid()
        for storage_key in self._active_storage_keys():
            state = self._load_storage_state(storage_key)
            if state.get("active") and state.get("owner_pid") == current_pid:
                return True
        return False

    def _save_state(self, key: str, state: dict[str, Any]) -> None:
        self._save_storage_state(_state_storage_key(key), state, key_hint=key)

    def _save_storage_state(
        self, storage_key: str, state: dict[str, Any], *, key_hint: str | None = None
    ) -> None:
        state = dict(state)
        self.ctx.state.set(storage_key, state)
        active_storage = self._active_storage_keys()
        if state.get("active"):
            if key_hint and state.get("owner_pid") == os.getpid():
                self._active_keys.add(key_hint)
            if storage_key not in active_storage:
                active_storage.append(storage_key)
        else:
            self._active_keys = {
                key
                for key in self._active_keys
                if _state_storage_key(key) != storage_key
            }
            if key_hint:
                self._active_keys.discard(key_hint)
            active_storage = [item for item in active_storage if item != storage_key]
        self._set_active_storage_keys(active_storage)

    @staticmethod
    def _linked_command_storage_keys(state: dict[str, Any]) -> list[str]:
        value = state.get("command_session_storage_keys")
        if not isinstance(value, list):
            return []
        return [
            item
            for item in value[:256]
            if isinstance(item, str) and item.startswith("session:")
        ]

    def _save_command_state(
        self, raw_key: str, storage_key: str, state: dict[str, Any]
    ) -> None:
        """Save a command mutation to its canonical UI state and linked sk copies."""
        self._save_storage_state(storage_key, state, key_hint=raw_key)
        for linked_storage in self._linked_command_storage_keys(state):
            if linked_storage != storage_key:
                self._save_storage_state(linked_storage, state)

    def _command_state(
        self, raw_key: str, surface: str = ""
    ) -> tuple[str, dict[str, Any], bool]:
        """Resolve a command-only sk key to the one UI state that linked it."""
        storage_key = _state_storage_key(raw_key)
        if raw_key.startswith("sk:"):
            matches = []
            canonical_active = False
            for candidate in self._active_storage_keys():
                state = self._load_storage_state(candidate)
                if (
                    state.get("active")
                    and state.get("canonical_ui_storage_key") == candidate
                ):
                    canonical_active = True
                    if storage_key in self._linked_command_storage_keys(state):
                        matches.append((candidate, state))
            if len(matches) == 1:
                candidate, state = matches[0]
                return candidate, state, False
            direct = self._load_storage_state(storage_key)
            ui_surface = str(surface or "").strip().lower() in {
                "tui",
                "desktop",
                "dashboard",
            }
            return storage_key, direct, bool(
                ui_surface and canonical_active and not direct.get("active")
            )
        return storage_key, self._load_storage_state(storage_key), False

    def _link_command_key(
        self, state_key: str, state: dict[str, Any], command_key: str | None
    ) -> None:
        if not command_key or not command_key.startswith("sk:"):
            return
        linked = self._linked_command_storage_keys(state)
        command_storage = _state_storage_key(command_key)
        canonical_storage = _state_storage_key(state_key)
        if (
            command_storage in linked
            and state.get("canonical_ui_storage_key") == canonical_storage
        ):
            return
        if command_storage not in linked:
            linked.append(command_storage)
        for evicted in linked[:-256]:
            if evicted != canonical_storage:
                self._clear_storage_key(evicted)
        state["command_session_storage_keys"] = linked[-256:]
        state["canonical_ui_storage_key"] = canonical_storage
        self._save_state(state_key, state)

    @staticmethod
    def _session_id_hash(value: Any) -> str:
        text = str(value or "").strip()
        return hashlib.sha256(text.encode("utf-8")).hexdigest() if text else ""

    def _remember_session_id(self, key: str, state: dict[str, Any], value: Any) -> None:
        digest = self._session_id_hash(value)
        if digest and state.get("session_id_hash") != digest:
            state["session_id_hash"] = digest
            self._save_state(key, state)

    def _claim_state_for_current_process(
        self, key: str, state: dict[str, Any]
    ) -> None:
        if state.get("active") and state.get("owner_pid") != os.getpid():
            state["owner_pid"] = os.getpid()
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
                "Plan mode activation was refused: this server surface lacks the required "
                "session binding. Upgrade Hermes to a version containing the TUI/gateway "
                "plugin-command session-binding fix."
            )
        return identity, None

    def _state_for_hook(self, identity: SessionIdentity) -> tuple[str | None, dict[str, Any]]:
        """Return active state, including the required legacy CLI fail-closed fallback."""
        if identity.key:
            state = self._load_state(identity.key)
            if state.get("active"):
                self._claim_state_for_current_process(identity.key, state)
                self._link_command_key(identity.key, state, identity.fallback_key)
                return identity.key, state
        if identity.key and identity.fallback_key:
            fallback_state = self._load_state(identity.fallback_key)
            if fallback_state.get("active"):
                self._claim_state_for_current_process(
                    identity.fallback_key, fallback_state
                )
                self._link_command_key(
                    identity.key, fallback_state, identity.fallback_key
                )
                self._save_state(identity.key, fallback_state)
                return identity.key, fallback_state
        cli_key = f"cli:{os.getpid()}"
        if identity.key != cli_key:
            cli_state = self._load_state(cli_key)
            if cli_state.get("active"):
                self._claim_state_for_current_process(cli_key, cli_state)
                if identity.key and identity.key.startswith("ui:"):
                    linked = self._linked_command_storage_keys(cli_state)
                    cli_storage = _state_storage_key(cli_key)
                    if cli_storage not in linked:
                        linked.append(cli_storage)
                    cli_state["command_session_storage_keys"] = linked[-256:]
                    cli_state.pop("cli_pid", None)
                    self._link_command_key(
                        identity.key, cli_state, identity.fallback_key
                    )
                    self._save_state(identity.key, cli_state)
                    return identity.key, cli_state
                return cli_key, cli_state
        return identity.key, self._load_state(identity.key) if identity.key else {}

    def _fixed_plans_dir(self) -> str:
        cwd_reader = _runtime_cwd_reader()
        raw = str(cwd_reader() if cwd_reader else "").strip()
        reader = _session_reader()
        if not raw:
            raw = str(reader("TERMINAL_CWD", "") if reader else "").strip()
        if not raw or not os.path.isabs(raw) or not os.path.isdir(raw):
            raw = os.getcwd()
        base = os.path.realpath(raw)
        hermes_path = os.path.join(base, ".hermes")
        plan_path = os.path.join(hermes_path, "plans")
        if os.path.islink(hermes_path) or os.path.islink(plan_path):
            raise ValueError("the .hermes plan path contains a symlink")
        os.makedirs(plan_path, exist_ok=True)
        expected = os.path.join(os.path.realpath(base), ".hermes", "plans")
        if (
            os.path.islink(hermes_path)
            or os.path.islink(plan_path)
            or os.path.realpath(plan_path) != expected
        ):
            raise ValueError("the .hermes plan path contains a symlink")
        return expected

    @staticmethod
    def _plan_files(state: dict[str, Any]) -> list[str]:
        plans_dir = state.get("plans_dir")
        if not isinstance(plans_dir, str) or not os.path.isdir(plans_dir):
            return []
        remembered = state.get("plan_files")
        if not isinstance(remembered, list):
            return []
        files = []
        for value in remembered[:256]:
            if (
                isinstance(value, str)
                and value.endswith(".md")
                and _path_is_inside(value, plans_dir)
                and os.path.isfile(value)
            ):
                files.append(os.path.realpath(value))
        return files

    def _remember_plan_targets(
        self, key: str, state: dict[str, Any], targets: list[str]
    ) -> None:
        remembered = state.get("plan_files")
        files = [item for item in remembered if isinstance(item, str)] if isinstance(remembered, list) else []
        for target in targets:
            resolved = os.path.realpath(target)
            if not resolved.endswith(".md"):
                continue
            files = [item for item in files if item != resolved]
            files.append(resolved)
        state["plan_files"] = files[-256:]
        self._save_state(key, state)

    @staticmethod
    def _status_text(state: dict[str, Any], files: list[str]) -> str:
        mode = "on" if state.get("active") else "off"
        entered = state.get("entered_at") or "not set"
        plans_dir = state.get("plans_dir") or "not set"
        rendered = "\n".join(f"- {path}" for path in files) or "- none"
        return (
            f"Plan mode: {mode}\nEntered at: {entered}\n"
            f"Plans directory: {plans_dir}\nPlan files:\n{rendered}"
        )

    @staticmethod
    def _agent_owned(state: dict[str, Any]) -> bool:
        """True only for the activation the agent's own ``plan_mode`` call created."""
        activation = state.get("activation_id")
        return bool(
            state.get("active")
            and state.get("entered_by") == "agent"
            and activation
            and state.get("agent_activation_id") == activation
        )

    def tool(self, args: Any = None, **kwargs: Any) -> str:
        """Agent-callable ``plan_mode``: on, status or off; approval stays the user's act."""
        call_args = args if isinstance(args, dict) else {}
        action = str(call_args.get("action") or "").strip().lower()
        if action in {"on", "status", "off"}:
            reason = str(call_args.get("reason") or "").strip()
            message = self._run_command(action, reason, entered_by="agent")
        else:
            message = (
                "Unsupported plan_mode action: use on, status or off. Approving or "
                "rejecting a plan is the user's act (/planmode approve or /planmode reject)."
            )
        return json.dumps({"message": message}, ensure_ascii=False)

    def command(self, raw_args: str) -> str:
        raw = str(raw_args or "").strip()
        action, _, remainder = raw.partition(" ")
        return self._run_command(action.lower() or "status", remainder.strip())

    def _run_command(self, action: str, remainder: str, entered_by: str = "user") -> str:
        """Resolve identity like the slash command, then apply one plan-mode action."""
        identity, error = self._identity_or_reply()
        if error:
            if action == "status":
                reason = error.removeprefix("Plan mode activation was refused: ")
                reason = reason.removeprefix("Plan mode is unavailable: ")
                return f"Plan mode is unavailable on this surface: {reason}"
            return error
        assert identity.key is not None

        if action == "on" and identity.inherited_env and _gateway_process_is_admitted():
            return (
                "Plan mode activation was refused: this gateway/slash-worker process "
                "only exposed inherited session identity, so activation cannot be "
                "bound safely to one session."
            )
        if action in {"on", "off", "approve", "reject"}:
            # Every state-mutating command must run in the plugin instance of the
            # session's own profile; status stays read-only and is not gated.
            refused = (
                "Plan mode activation was refused"
                if action == "on"
                else f"Plan mode command '{action}' was refused"
            )
            reader = _session_reader()
            session_profile = str(
                reader("HERMES_SESSION_PROFILE", "") if reader else ""
            ).strip()
            if session_profile and self._registration_profile is None:
                return (
                    f"{refused}: the plugin registration profile "
                    "is unknown, so it cannot be matched safely to the bound session "
                    f"profile '{session_profile}'."
                )
            if (
                session_profile
                and self._registration_profile
                and session_profile != self._registration_profile
            ):
                return (
                    f"{refused}: this plugin instance is registered "
                    f"for profile '{self._registration_profile}', but the session belongs "
                    f"to profile '{session_profile}'."
                )

        with self._lock:
            command_storage_key, state, unresolved_ui_command = self._command_state(
                identity.key, identity.surface
            )
            if action == "status" and unresolved_ui_command:
                return (
                    "Plan mode: unresolved\nA TUI/Desktop plan-mode state is active, but "
                    "Hermes did not bind the stable UI session ID, so this command's session "
                    "key is not linked to it and enforcement may apply to this tab. Run one "
                    "turn in the owning tab, then retry; the plugin will not guess across tabs."
                )
            if action != "status" and unresolved_ui_command:
                return (
                    "Plan mode command was refused: Hermes did not bind the stable UI "
                    "session ID, so this command cannot be matched safely to the tab that "
                    "owns plan mode. Retry from that tab after Hermes exposes its stable UI "
                    "identity; the plugin will not guess across tabs."
                )
            if action == "on":
                if entered_by == "agent" and state.get("active"):
                    return self._status_text(state, self._plan_files(state))
                try:
                    plans_dir = self._fixed_plans_dir()
                except (OSError, ValueError) as exc:
                    return f"Plan mode activation was refused: {exc}."
                preserved = {
                    key: state[key]
                    for key in (
                        "canonical_ui_storage_key",
                        "command_session_storage_keys",
                        "cli_pid",
                        "session_id_hash",
                    )
                    if key in state
                }
                state = {
                    "active": True,
                    "entered_at": _utc_now(),
                    "plans_dir": plans_dir,
                    "task": remainder,
                    "pending_note": "",
                    "plan_files": state.get("plan_files", []),
                    "owner_pid": os.getpid(),
                    "activation_id": uuid.uuid4().hex,
                    "entered_by": entered_by,
                    **preserved,
                }
                if entered_by == "agent":
                    state["agent_activation_id"] = state["activation_id"]
                if identity.key.startswith("cli:"):
                    state["cli_pid"] = os.getpid()
                self._save_command_state(identity.key, command_storage_key, state)
                # A UI turn's tool call links its sk: alias now, so the tab's slash commands find it.
                self._link_command_key(identity.key, state, identity.fallback_key)
                task_text = f" Task: {remainder}" if remainder else ""
                return (
                    f"Plan mode is on for this session.{task_text}\n"
                    f"Write plans only to absolute paths under {plans_dir}.\n"
                    "Use YYYY-MM-DD_HHMMSS-<slug>.md. Read-only planning tools are allowed; "
                    "terminal, code execution, delegation, connectors/MCP, messaging, browser "
                    "mutations, and unknown tools are blocked until /planmode approve."
                )

            if action == "status":
                return self._status_text(state, self._plan_files(state))

            if action == "approve":
                if not state.get("active"):
                    return "Plan mode is not on for this session."
                files = self._plan_files(state)
                if not files:
                    return (
                        "Plan approval was refused: this session has no tracked plan file. "
                        f"Write the plan under {state.get('plans_dir') or '.hermes/plans'} "
                        "first, or use /planmode off."
                    )
                if remainder:
                    candidate = remainder if os.path.isabs(remainder) else os.path.join(
                        str(state.get("plans_dir") or ""), remainder
                    )
                    candidate = os.path.realpath(candidate)
                    if candidate not in files:
                        return (
                            "Plan approval was refused: the requested file was not written "
                            "by this session."
                        )
                    approved_path = candidate
                else:
                    approved_path = files[-1]
                state["active"] = False
                state.pop("activation_id", None)
                state["pending_note"] = (
                    f"The user approved the plan at {approved_path}. Implement it now."
                )
                self._save_command_state(identity.key, command_storage_key, state)
                return f"Plan approved. Plan mode is off. Next turn will implement {approved_path}."

            if action == "reject":
                if not state.get("active"):
                    return "Plan mode is not on for this session."
                feedback = remainder or "No additional feedback was provided."
                state["pending_note"] = f"The user rejected the plan: {feedback}. Revise it."
                # Once the user weighs in, approve/reject governs: the agent can no longer end it.
                state["entered_by"] = "user"
                state.pop("agent_activation_id", None)
                self._save_command_state(identity.key, command_storage_key, state)
                return (
                    "Plan rejected. Plan mode remains on; the feedback will be injected next turn. "
                    "Plan mode is now user-owned; only /planmode approve, reject or off can end it."
                )

            if action == "off":
                if entered_by == "agent" and not state.get("active"):
                    return "Plan mode is not on for this session."
                if entered_by == "agent" and not self._agent_owned(state):
                    return (
                        "Plan mode was entered by the user; only /planmode approve, "
                        "reject or off can end it."
                    )
                state["active"] = False
                for field in ("activation_id", "entered_by", "agent_activation_id"):
                    state.pop(field, None)
                state["pending_note"] = ""
                self._save_command_state(identity.key, command_storage_key, state)
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
            with self._lock:
                self._prune_dead_cli_states()
            identity = derive_session_identity(str(kwargs.get("platform") or ""))
            if identity.unsupported:
                return None
            if identity.non_cli_without_key or not identity.key:
                if _cron_session_is_active():
                    return None
                with self._lock:
                    cli_active = self._load_state(f"cli:{os.getpid()}").get("active")
                    durable_active = self._current_process_has_active_state()
                    process_active = bool(self._active_keys)
                if process_active or durable_active or cli_active:
                    return _block_message(
                        tool_name or "unknown tool",
                        "The active session key could not be derived, so this request was blocked fail-closed.",
                    )
                return None

            with self._lock:
                state_key, state = self._state_for_hook(identity)
                if not state.get("active"):
                    self._active_keys.discard(identity.key)
                    return None
                assert state_key is not None
                self._active_keys.add(state_key)
                self._remember_session_id(state_key, state, kwargs.get("session_id"))

                name = str(tool_name or "")
                call_args = args if isinstance(args, dict) else {}
                if (
                    name in READ_ONLY_TOOLS
                    or name == PLAN_MODE_TOOL
                    or name in self._extra_allowed_tools()
                ):
                    return None
                if name == "skill_view":
                    reason = _skill_view_block_reason()
                    return _block_message(name, reason) if reason else None
                if name in PLAN_WRITERS:
                    targets = _write_targets(name, call_args)
                    plans_dir = state.get("plans_dir")
                    if not targets or not isinstance(plans_dir, str):
                        return _block_message(name, "Every write target must be explicit and absolute.")
                    if not _plans_dir_is_still_safe(plans_dir):
                        return _block_message(
                            name,
                            "The plans directory changed after activation; reactivate plan mode "
                            "only after restoring a non-symlink plan root.",
                        )
                    if all(_path_is_inside(target, plans_dir) for target in targets):
                        call_id = str(kwargs.get("tool_call_id") or "")
                        if not call_id:
                            # Hosts without tool_call_id keep 0.1.6 pre-write tracking.
                            self._remember_plan_targets(state_key, state, targets)
                            return None
                        pending = self._pending_plan_writes
                        pending_key = (str(kwargs.get("session_id") or ""), call_id)
                        pending[pending_key] = (state_key, list(targets), state.get("activation_id"))
                        while len(pending) > 256:
                            pending.pop(next(iter(pending)))
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

    def post_tool_call(self, tool_name: str = "", args: Any = None, **kwargs: Any) -> None:
        """Make a pending plan write approvable only after Hermes reports ``ok``."""
        try:
            call_id = str(kwargs.get("tool_call_id") or "")
            with self._lock:
                pending = self._pending_plan_writes.pop(
                    (str(kwargs.get("session_id") or ""), call_id), None
                )
                if pending is None or kwargs.get("status") != "ok":
                    return
                state_key, targets, activation_id = pending
                call_args = args if isinstance(args, dict) else {}
                if _write_targets(str(tool_name or ""), call_args) != targets:
                    return
                state = self._load_state(state_key)
                # Only the activation that allowed the write may track it.
                if state.get("active") and state.get("activation_id") == activation_id:
                    self._remember_plan_targets(state_key, state, targets)
        except Exception:
            return  # An observer failure leaves the write unapprovable (fail-closed).

    def pre_llm_call(self, **kwargs: Any) -> dict[str, str] | None:
        try:
            with self._lock:
                self._prune_dead_cli_states()
            identity = derive_session_identity(str(kwargs.get("platform") or ""))
            if identity.unsupported:
                return None
            if identity.non_cli_without_key or not identity.key:
                if _cron_session_is_active():
                    return None
                with self._lock:
                    cli_active = self._load_state(f"cli:{os.getpid()}").get(
                        "active"
                    )
                    durable_active = self._current_process_has_active_state()
                    process_active = bool(self._active_keys)
                if process_active or durable_active or cli_active:
                    return {
                        "context": (
                            "Plan mode is active in this process, but this turn's session key "
                            "could not be derived. Tool calls will be blocked fail-closed."
                        )
                    }
                return None

            with self._lock:
                state_key, state = self._state_for_hook(identity)
                parts: list[str] = []
                pending = state.get("pending_note")
                if isinstance(pending, str) and pending.strip():
                    parts.append(pending.strip())
                    state["pending_note"] = ""
                    assert state_key is not None
                    self._save_state(state_key, state)
                if state.get("active"):
                    assert state_key is not None
                    self._active_keys.add(state_key)
                    self._remember_session_id(state_key, state, kwargs.get("session_id"))
                    plans_dir = state.get("plans_dir")
                    parts.append(
                        "Plan mode is ON. Explore with the allowed read-only tools and write only "
                        f"plan Markdown files using absolute paths under {plans_dir}. Name each plan "
                        "YYYY-MM-DD_HHMMSS-<slug>.md. Do not implement or call blocked tools; ask "
                        "the user to approve with /planmode approve when the plan is ready."
                        + (f" {_AGENT_NOTE}" if self._agent_owned(state) else "")
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
        platform = str(kwargs.get("platform") or "").strip().lower()
        with self._lock:
            if platform in {"cli", "terminal"}:
                cli_key = f"cli:{os.getpid()}"
                cli_storage = _state_storage_key(cli_key)
                self._clear_state_family(
                    cli_storage, self._load_storage_state(cli_storage)
                )
                if identity.key == cli_key:
                    return
            if identity.key and not identity.non_cli_without_key:
                if identity.key.startswith("sk:"):
                    storage_key, state, _ = self._command_state(
                        identity.key, identity.surface
                    )
                else:
                    storage_key = _state_storage_key(identity.key)
                    state = self._load_storage_state(storage_key)
                self._clear_state_family(storage_key, state)
                return

            # Gateway's reset callback may run outside the command's bound
            # ContextVar scope. Match the hashed old session id when possible;
            # never guess from active-session count because another session's
            # reset must not disable this one.
            active_storage = self._active_storage_keys()
            old_digest = self._session_id_hash(
                kwargs.get("old_session_id")
            )
            matches: dict[str, dict[str, Any]] = {}
            if old_digest:
                for storage_key in active_storage:
                    state = self.ctx.state.get(storage_key, {})
                    if isinstance(state, dict) and state.get("session_id_hash") == old_digest:
                        canonical = state.get("canonical_ui_storage_key")
                        family_key = (
                            canonical
                            if isinstance(canonical, str)
                            and canonical.startswith("session:")
                            else storage_key
                        )
                        matches[family_key] = state
            if len(matches) == 1:
                storage_key, state = next(iter(matches.items()))
                self._clear_state_family(storage_key, state)

    def on_session_finalize(self, **kwargs: Any) -> None:
        identity = derive_session_identity(str(kwargs.get("platform") or ""))
        if identity.unsupported:
            return
        cli_key = f"cli:{os.getpid()}"
        platform = str(kwargs.get("platform") or "").strip().lower()
        if identity.key == cli_key or platform in {"cli", "terminal"}:
            with self._lock:
                self._clear_storage_key(_state_storage_key(cli_key))
