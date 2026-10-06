"""Pure turn-note and text-first reply helpers; no Hermes dependencies."""
from __future__ import annotations

from datetime import datetime
import json


AGENT_HINT = (
    'For multi-step or risky changes you can enter enforced plan mode with the plan_mode tool '
    '(action "on"; find it with tool_search) and submit a plan for the user\'s approval before building.'
)
BUILTIN_OVERRIDE = (
    "This overrides the /plan instruction to offer execution: submit the plan with plan_mode instead."
)
COMPACT_PLAN = (
    "Make it compact and decision-complete, sized to the task, so the user can review it in a minute: Goal (one line); "
    "Decisions and assumptions (including clarify answers); Changes (each file and what changes in it); Steps (numbered, "
    "each with how it is checked); Validation (the exact commands). Name exact files, functions and commands, but include "
    "code only where an exact signature, format or pattern is itself the decision. This format replaces any other plan "
    "template or plan-writing guidance for this plan."
)
NO_COMMITS = (
    "Do not commit, and do not plan commit steps, unless the user asked for commits; leave the changes for the user to review."
)
LOCAL_PLATFORMS = frozenset({
    "cli", "terminal", "tui", "desktop", "dashboard", "api_server", "webhook",
    "acp", "local", "batch", "cron", "subagent", "curator",
    # core's non-messaging surfaces (gateway.session_context.NON_MESSAGING_SESSION_SURFACES)
    "codex", "gateway", "kanban", "msgraph_webhook", "tool",
})


def plan_file_stamp(now=None) -> str:
    """Local timestamp for plan file names; the model cannot read the clock while terminal is blocked."""
    return (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S")


def planning_note(plans_dir, now=None, *, style="core", plan_skill="", commits=True) -> str:
    stamp = plan_file_stamp(now)
    target = f"{plans_dir}/{stamp}-<slug>.md (that timestamp is current; do not look up the time)"
    if plan_skill:
        fallback = COMPACT_PLAN if style == "compact" else "Write it with numbered steps."
        write = (f"2. Load the {plan_skill} skill with skill_view and write the plan in its format, as Markdown to {target}. "
                 "Its format replaces any other plan template or plan-writing guidance for this plan. "
                 f"If the skill cannot be loaded, use this format instead: {fallback}")
    elif style == "compact":
        write = f"2. Write the plan as Markdown to {target}. {COMPACT_PLAN}"
    else:
        write = f"2. Write the plan as Markdown to {target}, with numbered steps."
    if not commits:
        write += f" {NO_COMMITS}"
    return (
        f"Plan mode is ON: only read-only tools work, and files may be written only under {plans_dir}.\n"
        "1. Explore with read-only tools first and settle every fact the files can answer yourself. Then, before writing the plan, "
        "ask with the clarify tool about each open choice only the user can make (a preference or tradeoff that changes what "
        "gets built, such as behaviour, policy, format or scope): up to 4 short choices, recommended first. Do not guess these; "
        "if one goes unanswered, take the recommended choice and record it in the plan as an assumption.\n"
        f"{write}\n"
        '3. Show the complete plan in your reply, then call plan_mode(action="submit") on its own to ask the user to approve it (if plan_mode is not loaded, find it with tool_search "plan_mode"). Approval starts implementation in this same turn.\n'
        '4. Do not implement before approval and do not ask "should I proceed?" in prose. A denial is review feedback, not a refusal: revise the plan and submit a complete new revision.\n'
        "In group chats, keep secrets and private details out of the plan."
    )


def executing_pointer(state: dict, *, commits=True) -> str:
    revision = state.get("approved_revision")
    rev = f" (rev {revision})" if revision is not None else ""
    return (
        f"Executing the approved plan {state.get('approved_path')}{rev}. "
        "Keep todo_list statuses current; re-read the plan if your context was compacted."
    ) + ("" if commits else f" {NO_COMMITS}")


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
