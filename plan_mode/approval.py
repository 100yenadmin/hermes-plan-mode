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
    return " ".join(re.sub(r"[*_`~]", "", re.sub(r"^\s*#+\s+", "", text)).split())


def _title(text, summary, name) -> str:
    heading = re.search(r"^\s*#{1,6}\s+(.+)$", text, re.MULTILINE)
    title = _plain(summary) if str(summary).strip() else _plain(heading[1]) if heading else name
    return re.sub(r"(?i)^plan(?:\s*\([^)]*\))?(?:\s*:\s*|\s+[—–-]\s+)", "", title) or name


# Platforms whose approval card cuts the whole prompt at a fixed size (WhatsApp Cloud: 1024-char body).
_CARD_LIMITS = {"whatsapp_cloud": 1000}


# Section headings that hold the work itself (core /plan asks for "Step-by-step tasks"), and section labels that never
# do. A label heading is made only of meta words ("Tests / validation"), so a task such as "Test endpoint" is kept.
_STEP_SECTION = re.compile(r"(?i)\b(steps?|tasks?|implementation|to-?dos?|milestones?|phases?|execution)\b")
_APPROACH_SECTION = re.compile(r"(?i)\b(approach|plan)\b")
_META_WORD = (r"(?:goals?|current context|context|assumptions|background|summary|overview|"
              r"architecture(?:\s*/\s*proposed approach)?|tests?|testing|validation|verification|risks?|tradeoffs|open questions|notes?|"
              r"out of scope|non-goals|files(?: likely to change)?)")
_META_SECTION = re.compile(rf"(?i)^{_META_WORD}(?:\s*(?:[/,&]|\band\b)\s*(?:{_META_WORD})?)*:?$")
_STEP_PREFIX = re.compile(r"(?i)^(?:step|phase|task)\s*\d+[a-z]?\s*[:.)—–-]*\s*")
_HEADING = re.compile(r"^\s*(#{1,6})\s+(.+?)(?:\s+#+)?\s*$")
_LIST_ITEM = re.compile(r"^(?:\d+[.)]|[-*+])\s+(.+)$")


def _unfenced(text: str) -> list[str]:
    """The plan's lines with fenced code blanked, so a `# comment` in a snippet is not a heading."""
    lines, fence = [], ""
    for line in text.splitlines():
        marker = re.match(r"^\s*(`{3,}|~{3,})(.*)$", line)
        if fence:
            # Only a bare marker of the opener's kind and at least its length closes the fence.
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = ""
            lines.append("")
        elif marker:
            fence = marker[1]
            lines.append("")
        else:
            lines.append(line)
    return lines


def _steps(text: str) -> list[str]:
    """Pick the plan's steps for a short chat summary, not its section headings."""
    lines = _unfenced(text)
    # One pass: (line, level, text, label is meta or sits under one), each heading's direct children and
    # section end, and whether its section holds numbered items / its own body holds bullets (meta parts excluded).
    headings, children, ends, numbered, bullets, open_ = [], [], [], [], [], []
    for index, line in enumerate(lines):
        if (match := _HEADING.match(line)):
            level, heading = len(match[1]), _plain(match[2])
            while open_ and headings[open_[-1]][1] >= level:
                ends[open_.pop()] = index
            meta = bool(_META_SECTION.match(heading)) or bool(open_ and headings[open_[-1]][3])
            if open_:
                children[open_[-1]].append(len(headings))
            headings.append((index, level, heading, meta))
            children.append([]); ends.append(len(lines)); numbered.append(False); bullets.append(False)
            open_.append(len(headings) - 1)
        elif open_ and not headings[open_[-1]][3] and _LIST_ITEM.match(line):
            if line[0].isdigit():
                for position in open_:
                    numbered[position] = True
            else:
                bullets[open_[-1]] = True

    def section_items(position: int) -> list[str]:
        named = [headings[child][2] for child in children[position] if not headings[child][3]]
        if named:
            return named  # direct sub-headings are the steps; their own sub-headings are details
        body_end = headings[children[position][0]][0] if children[position] else ends[position]
        return [match[1] for line in lines[headings[position][0] + 1:body_end] if (match := _LIST_ITEM.match(line))]

    def clean(items: list[str]) -> list[str]:
        return [_STEP_PREFIX.sub("", _plain(item)) or _plain(item) for item in items]

    for pattern in (_STEP_SECTION, _APPROACH_SECTION):
        for position, (_, level, heading, meta) in enumerate(headings):
            if level > 1 and not meta and pattern.search(heading) and not _STEP_PREFIX.match(heading):
                items = section_items(position)
                if items:
                    return clean(items)
    step_headings = [heading for _, level, heading, meta in headings
                     if level > 1 and not meta and _STEP_PREFIX.match(heading)]
    if step_headings:
        return clean(step_headings)
    # No step section, in document order: numbered items; sub-headings that hold no list; the bullets of a
    # sub-heading that holds no numbered list. Anything under a label heading ("Risks") is skipped.
    by_line = {heading[0]: position for position, heading in enumerate(headings)}
    ordered, current = [], None
    for index, line in enumerate(lines):
        if index in by_line:
            current = by_line[index]
            _, level, heading, meta = headings[current]
            if 2 <= level <= 3 and not meta and not numbered[current] and not bullets[current]:
                ordered.append(heading)
        elif (match := _LIST_ITEM.match(line)) and not (current is not None and headings[current][3]):
            if line[0].isdigit() or (current is not None and 2 <= headings[current][1] <= 3
                                     and not numbered[current]):
                ordered.append(match[1])
    if ordered:
        return ordered
    bullet_items = [match[1] for line in lines if (match := re.match(r"^[-*+]\s+(.+)$", line))]
    return bullet_items or [line for line in text.splitlines() if line.strip()]


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
        steps = _steps(text)
        result = f"Plan rev {revision}: {title}"
        result += "".join(f"\n{index}. {_plain(step)}" for index, step in enumerate(steps[:6], 1))
        return result if len(result) <= 250 else result[:249] + "…"
    limit = _CARD_LIMITS.get(str(platform).lower(), 3500)
    prefix = f"Plan rev {revision} ({name}) — approve to start implementing, deny to keep planning.\n\n"
    result = prefix + text
    if len(result) <= limit:
        return result
    suffix = f"\n… (truncated; full plan: {path})"
    return result[:max(0, limit - len(suffix))] + suffix[:limit]


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
