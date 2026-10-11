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
CODEX_COMPACT_PLAN = (
    "Summary; Key changes, grouped by behaviour or subsystem, naming every touched file and every consumer of a "
    "changed interface; Test plan (exact commands); Assumptions. Concise by default; a straightforward change fits "
    "in about 5 bullets; expand only when asked. Do not include time estimates, RED/GREEN steps, unchanged-file "
    "lists, or repeating repo facts. This format replaces any other plan template or plan-writing guidance for this plan."
)
LEAN_CRAFT = (
    "This supersedes the core /plan craft: no 2–5-minute task slicing, no copy-pasteable full code, and no per-task "
    "red/green TDD cycle or deliberately failing test runs unless the user asked for TDD. Do not load a TDD skill "
    "unless asked. Add or update tests for changed behaviour and run them after implementing. Write the plan file "
    "only at the absolute path given above, never the relative .hermes/plans path from the /plan instructions."
)
LEAN_EXECUTION = "Implement directly, with no red-first ceremony unless the user asked for TDD."
LOCAL_PLATFORMS = frozenset({
    "cli", "terminal", "tui", "desktop", "dashboard", "api_server", "webhook",
    "acp", "local", "batch", "cron", "subagent", "curator",
    # core's non-messaging surfaces (gateway.session_context.NON_MESSAGING_SESSION_SURFACES)
    "codex", "gateway", "kanban", "msgraph_webhook", "tool",
})


def plan_file_stamp(now=None) -> str:
    """Local timestamp for plan file names; the model cannot read the clock while terminal is blocked."""
    return (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S")


def planning_note(plans_dir, now=None, *, style="core", plan_skill="", commits=True,
                  levers=frozenset(), previous_plan="", platform="") -> str:
    stamp = plan_file_stamp(now)
    target = f"{plans_dir}/{stamp}-<slug>.md (that timestamp is current; do not look up the time)"
    compact = CODEX_COMPACT_PLAN if "codex_compact" in levers else COMPACT_PLAN
    if plan_skill:
        fallback = compact if style == "compact" else "Write it with numbered steps."
        write = (f"2. Load the {plan_skill} skill with skill_view and write the plan in its format, as Markdown to {target}. "
                 "Its format replaces any other plan template or plan-writing guidance for this plan. "
                 f"If the skill cannot be loaded, use this format instead: {fallback}")
    elif style == "compact":
        write = f"2. Write the plan as Markdown to {target}. {compact}"
    else:
        write = f"2. Write the plan as Markdown to {target}, with numbered steps."
    if not commits:
        write += f" {NO_COMMITS}"
    note = (
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
    if "question_shape" in levers:
        start, end = note.index("Then, before writing the plan,"), note.index("\n2.")
        note = note[:start] + (
            "Ask only about choices that materially change the plan and cannot be settled by reading the repo. "
            "Prefer 1 question, at most 3; offer 2–3 choices, each written label — one-line consequence, "
            "recommended first. Otherwise record an assumption."
        ) + note[end:]
    if "plan_once" in levers and str(platform).strip().lower() == "mattermost":
        note = note.replace(
            '3. Show the complete plan in your reply, then call',
            '3. If the plan is at most 3,000 characters, show only a 3–5 line summary plus the path in your reply; '
            'the approval prompt contains the full plan. For a longer plan, show the complete plan in your reply. Then call',
        )
    if "lean_craft" in levers and style == "compact" and not plan_skill:
        note += "\n" + LEAN_CRAFT
    if "sticky" in levers:
        note += '\nWhile plan mode is on, a request to implement or "just do it" means plan the implementation and submit; do not call blocked tools.'
    if "reentry" in levers and previous_plan:
        note += f"\nPrevious plan: {previous_plan}. Read it; refine it for the same task or start a new file for a different task."
    return note


def executing_pointer(state: dict, *, commits=True, levers=frozenset()) -> str:
    revision = state.get("approved_revision")
    rev = f" (rev {revision})" if revision is not None else ""
    result = (
        f"Executing the approved plan {state.get('approved_path')}{rev}. "
        "Keep todo_list statuses current; re-read the plan if your context was compacted."
    ) + ("" if commits else f" {NO_COMMITS}")
    if "todo_batch" in levers:
        result = result.replace("Keep todo_list statuses current;", "If using todo_list, batch status updates with the next real tool call;")
    if "lean_craft" in levers:
        result += " " + LEAN_EXECUTION
    return result


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
