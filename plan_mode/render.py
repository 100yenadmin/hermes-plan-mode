"""Pure turn-note and text-first reply helpers; no Hermes dependencies."""
from __future__ import annotations

import json


AGENT_HINT = (
    'For multi-step or risky changes you can enter enforced plan mode with the plan_mode tool '
    '(action "on"; find it with tool_search) and submit a plan for the user\'s approval before building.'
)
BUILTIN_OVERRIDE = (
    "This overrides the /plan instruction to offer execution: submit the plan with plan_mode instead."
)
LOCAL_PLATFORMS = frozenset({
    "cli", "terminal", "tui", "desktop", "dashboard", "api_server", "webhook",
    "acp", "local", "batch", "cron",
})


def planning_note(plans_dir) -> str:
    return (
        f"Plan mode is ON: only read-only tools work, and files may be written only under {plans_dir}.\n"
        "1. Explore with read-only tools. If a requirement is genuinely ambiguous, ask with the clarify tool (up to 4 short choices, recommended first) instead of guessing.\n"
        f"2. Write the plan as Markdown at an absolute path under {plans_dir}, named YYYY-MM-DD_HHMMSS-<slug>.md, with numbered steps.\n"
        '3. Show the complete plan in your reply, then call plan_mode(action="submit") on its own to ask the user to approve it (if plan_mode is not loaded, find it with tool_search "plan_mode"). Approval starts implementation in this same turn.\n'
        '4. Do not implement before approval and do not ask "should I proceed?" in prose. A denial is review feedback, not a refusal: revise the plan and submit a complete new revision.\n'
        "In group chats, keep secrets and private details out of the plan."
    )


def executing_pointer(state: dict) -> str:
    revision = state.get("approved_revision")
    rev = f" (rev {revision})" if revision is not None else ""
    return (
        f"Executing the approved plan {state.get('approved_path')}{rev}. "
        "Keep todo_list statuses current; re-read the plan if your context was compacted."
    )


def plan_text(path: str, text: str, revision, status: str) -> str:
    result = f"Plan: {path} (rev {revision}, {status})\n\n" + text
    if len(result) <= 3500:
        return result
    suffix = f"\n… (truncated; full plan at {path})"
    return result[:max(0, 3500 - len(suffix))] + suffix[:3500]


def todo_progress(result) -> dict | None:
    """Ignore malformed observations; derive counts from the observed items."""
    try:
        value = json.loads(result)
        todos = value.get("todos")
        if not isinstance(todos, list) or any(not isinstance(todo, dict) for todo in todos):
            return None
        current = next((todo.get("content", "") for todo in todos
                        if todo.get("status") == "in_progress"), "")
        if not isinstance(current, str):
            return None
        return {
            "total": len(todos),
            "completed": sum(todo.get("status") == "completed" for todo in todos),
            "cancelled": sum(todo.get("status") == "cancelled" for todo in todos),
            "current": current[:60],
        }
    except (TypeError, ValueError, AttributeError):
        return None


def response_footer(response_text: str, state: dict) -> str | None:
    last_line = response_text.rstrip().split("\n")[-1]
    if last_line.startswith(("⏸ Plan mode", "Plan progress", "Executing approved plan")):
        return None
    if state.get("active"):
        footer = "⏸ Plan mode: nothing changes until you approve the plan."
    elif state.get("phase") == "executing":
        progress = state.get("progress") or {}
        if progress.get("total", 0) > 0:
            done = progress.get("completed", 0) + progress.get("cancelled", 0)
            footer = f"Plan progress {done}/{progress['total']}"
            if progress.get("current"):
                footer += f" · now: {progress['current']}"
        else:
            revision = state.get("approved_revision")
            footer = "Executing approved plan" + (f" rev {revision}" if revision is not None else "")
    else:
        return None
    return response_text.rstrip() + "\n\n" + footer
