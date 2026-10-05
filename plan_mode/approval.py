"""Plan approval rendering and in-memory decision correlation; no Hermes imports."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import threading
import uuid


def make_rule_key(activation_id, revision, digest) -> str:
    act8 = re.sub(r"[^a-z0-9]", "", str(activation_id).lower())[:8].ljust(8, "0")
    key = f"plan-mode:{act8}:{revision}:{digest[:8]}:{uuid.uuid4().hex[:8]}"
    assert re.fullmatch(r"[a-z0-9:-]+", key)
    return key


def plan_digest(path) -> tuple[str, str]:
    with open(path, "rb") as stream:
        data = stream.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ValueError("The plan exceeds the 1 MiB approval limit.")
    return hashlib.sha256(data).hexdigest(), data.decode("utf-8", errors="replace")


def _plain(text: str) -> str:
    text = re.sub(r"!?\[([^\]]+)\]\([^)]*\)", r"\1", text)
    return " ".join(re.sub(r"[*_`#~]", "", text).split())


def _title(text, summary, name) -> str:
    heading = re.search(r"^\s*#{1,6}\s+(.+)$", text, re.MULTILINE)
    return _plain(summary) if str(summary).strip() else _plain(heading[1]) if heading else name


def approval_text(text, path, revision, platform, summary="") -> str:
    name = Path(path).name
    if str(platform).lower() in {"cli", "terminal"}:
        # The classic CLI streams the full plan just above its approval panel, and the panel
        # does not wrap embedded newlines, so it gets one line.
        title = _title(text, summary, name)[:160]
        return (f"Plan rev {revision} ({name}): {title}. The full plan is shown above; "
                "approve to start implementing, deny to keep planning.")
    if str(platform).lower() in {"telegram", "slack", "discord"}:
        title = _title(text, summary, name)
        steps = re.findall(r"^(?:[-*+]\s+|\d+[.)]\s+|#{2,3}\s+)(.+)$", text, re.MULTILINE)
        if not steps:
            steps = [line for line in text.splitlines() if line.strip()]
        result = f"Plan rev {revision}: {title}"
        result += "".join(f"\n{index}. {_plain(step)}" for index, step in enumerate(steps[:6], 1))
        return result if len(result) <= 250 else result[:249] + "…"
    prefix = f"Plan rev {revision} ({name}) — approve to start implementing, deny to keep planning.\n\n"
    result = prefix + text
    if len(result) <= 3500:
        return result
    suffix = f"\n… (truncated; full plan: {path})"
    return result[:max(0, 3500 - len(suffix))] + suffix[:3500]


class DecisionLedger:
    """Bounded, single-use decisions. Observer callbacks do no I/O and never raise."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, dict] = {}

    def mark_inflight(self, rule_key, tool_call_id) -> None:
        with self._lock:
            self._entries[rule_key] = {
                "choice": None, "cancelled": False,
                "tool_call_id": tool_call_id or None, "presented": False,
            }
            while len(self._entries) > 256:
                self._entries.pop(next(iter(self._entries)))

    def _record(self, kw, *, response: bool) -> None:
        try:
            pattern = kw.get("pattern_key")
            if not isinstance(pattern, str) or not pattern.startswith("plugin_rule:plan-mode:"):
                return
            with self._lock:
                entry = self._entries.get(pattern.removeprefix("plugin_rule:"))
                if entry is None:
                    return  # late/unrelated decisions cannot recreate a consumed entry
                if response:
                    choice = kw.get("choice")
                    entry["choice"] = choice if isinstance(choice, str) else None
                    entry["cancelled"] = bool(kw.get("cancelled"))
                else:
                    entry["presented"] = True
                if kw.get("tool_call_id"):
                    entry["tool_call_id"] = str(kw["tool_call_id"])
        except Exception:
            return

    def record_presented(self, **kw) -> None:
        self._record(kw, response=False)

    def record_response(self, **kw) -> None:
        self._record(kw, response=True)

    def take(self, rule_key) -> dict | None:
        with self._lock:
            return self._entries.pop(rule_key, None)

    def is_inflight(self, rule_key) -> bool:
        with self._lock:
            entry = self._entries.get(rule_key)
            return entry is not None and entry["choice"] is None


def is_human_approval(entry, tool_call_id) -> bool:
    return bool(
        entry
        and entry.get("choice") in {"once", "session", "always"}
        and not entry.get("cancelled")
        and (not entry.get("tool_call_id") or not tool_call_id
             or entry["tool_call_id"] == tool_call_id)
    )
