"""Pure turn-note and text-first reply helpers; no Hermes dependencies."""
from __future__ import annotations

from datetime import datetime
import json


AGENT_HINT = (
    'For multi-step or risky changes you can enter enforced plan mode with the plan_mode tool '
    '(action "on"; find it with tool_search) and submit a plan for the user\'s approval before building.'
)
AGENT_HINT_BRIDGE = (
    'For multi-step or risky changes, enter enforced plan mode first: plan_mode(action="on"), or if it is not in '
    'your tool list tool_call(calls=[{"name": "plan_mode", "arguments": {"action": "on"}}]).'
)
TEST_COMMANDS = (
    "Write test-plan commands the way this repo runs its tests (README, Makefile, pyproject or CI config). For a "
    "Python repo that documents none, use python3 -m pytest, not python, which many hosts do not have."
)
CHOICE_FIDELITY = (
    "The recommended choice must keep every requirement the user stated; offer a choice that relaxes one only as an "
    "alternative, and do not ask about a case the stated requirements already settle."
)
REFACTOR_SCOPE = (
    "For a rename or refactor, keep behaviour and existing test assertions unchanged apart from the rename itself, and "
    "add no new test cases unless asked. Leftover checks search the whole repo; plan files under {plans_dir} will "
    "match, so name them as expected matches instead of narrowing the search."
)
RENAME_SCOPE = (
    "For a rename, keep behaviour and existing test assertions unchanged apart from the rename itself, and add no new "
    "assertions unless asked. Check for leftovers with `git grep -n <old name>` (tracked files) plus any new files the "
    "rename creates. Plan files under {plans_dir} are normally untracked, so they do not match; leave them out of the "
    "plan. Outside a git repo, search the whole repo and name plan files as expected matches."
)
TEST_PLAN_ONLY = (
    "The test plan lists the commands that run the tests and check the requested behaviour; leave out git status, git "
    "diff and other repo-hygiene checks unless the user asked for them."
)
CHOICE_RECORD = "Record each answered clarify choice in Assumptions as the user's decision."
EDIT_BATCH = (
    "Make all changes to one file in a single edit call where the tool allows it, and send edits to different files, "
    "including the tests, in the same response."
)
BRIEF_FINAL = "When you finish, reply in 2–4 lines: what changed and the test result."
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
COMMIT_OVERRIDE = "This overrides any commit step in a loaded skill, such as a TDD skill's."
CODEX_COMPACT_PLAN = (
    "Summary; Key changes, grouped by behaviour or subsystem, naming every touched file and every consumer of a "
    "changed interface; Test plan (exact commands); Assumptions. Concise by default; a straightforward change fits "
    "in about 5 bullets; expand only when asked. Do not include time estimates, RED/GREEN steps, unchanged-file "
    "lists, or repeating repo facts. This format replaces any other plan template or plan-writing guidance for this plan."
)
PLAN_BUDGET = (
    "Keep it short: for a change to a few files, aim for under about 1,200 characters. Key changes get one bullet per "
    "file or behaviour, one or two sentences each, saying what changes and who consumes it, not how to write it (no "
    "code and no test-case lists; show a signature only where it changes). The test plan lists the commands only. "
    "Assumptions list only choices the user might disagree with; leave the section out if there are none."
)
LEAN_CRAFT = (
    "This supersedes the core /plan craft: no 2–5-minute task slicing, no copy-pasteable full code, and no per-task "
    "red/green TDD cycle or deliberately failing test runs unless the user asked for TDD. Do not load a TDD skill "
    "unless asked. Add or update tests for changed behaviour and run them after implementing. Write the plan file "
    "only at the absolute path given above, never the relative .hermes/plans path from the /plan instructions."
)
LEAN_EXECUTION = "Implement directly, with no red-first ceremony unless the user asked for TDD."
BRIDGE_SUBMIT = (
    '(if plan_mode is not in your tool list, call tool_call(calls=[{"name": "plan_mode", '
    '"arguments": {"action": "submit"}}]) directly; no tool_search or tool_describe is needed)'
)
BRIDGE_TODO = (
    ' If todo_list is not in your tool list, call it through tool_call with no lookup: '
    'tool_call(calls=[{"name": "todo_list", "arguments": {"todos": [{"id": "1", "content": "<step>", '
    '"status": "in_progress"}]}}]); add "merge": true when you update statuses.'
)
BLOCKED_TOOLS = (
    "Until the user approves, terminal, execute_code, delegation and MCP tools are blocked, so you cannot run "
    "commands or tests now: do not try them; read files instead and put the commands in the test plan."
)
BLOCKED_TOOL_MESSAGE = (
    "Plan mode is on: {name} is blocked until the user approves the plan. Commands and tests run only after "
    "approval: put them in the plan's test plan and do not retry blocked tools."
)
SKILL_POLICY = (
    "While planning, this overrides the system prompt's rule to load matching skills: plan mode sets the process "
    "and this plan format, so do not load skills about planning, plan writing, brainstorming, TDD, debugging, "
    "code review or codebase inspection. Load a skill only for facts this task needs, such as an API, a command "
    "or a repo convention."
)
SKILL_EXECUTION = "The approved plan is the process: do not load planning, TDD or code-review skills unless the user asked."
LIGHT_BRIDGE = (
    ' If plan_mode is not in your tool list: tool_call(calls=[{"name": "plan_mode", '
    '"arguments": {"action": "submit"}}]).'
)
LIGHT_BLOCKED = " Terminal and execute_code stay blocked until approval."
LIGHT_SKILL = " Do not load planning, TDD or debugging skills."
LOCAL_PLATFORMS = frozenset({
    "cli", "terminal", "tui", "desktop", "dashboard", "api_server", "webhook",
    "acp", "local", "batch", "cron", "subagent", "curator",
    # core's non-messaging surfaces (gateway.session_context.NON_MESSAGING_SESSION_SURFACES)
    "codex", "gateway", "kanban", "msgraph_webhook", "tool",
})


def no_commits(levers=frozenset()) -> str:
    return f"{NO_COMMITS} {COMMIT_OVERRIDE}" if "commit_override" in levers else NO_COMMITS


def plan_file_stamp(now=None) -> str:
    """Local timestamp for plan file names; the model cannot read the clock while terminal is blocked."""
    return (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S")


def planning_note(plans_dir, now=None, *, style="core", plan_skill="", commits=True,
                  levers=frozenset(), previous_plan="", platform="", reply_plan=False) -> str:
    stamp = plan_file_stamp(now)
    target = f"{plans_dir}/{stamp}-<slug>.md (that timestamp is current; do not look up the time)"
    compact = CODEX_COMPACT_PLAN if "codex_compact" in levers else COMPACT_PLAN
    if "plan_budget" in levers:
        compact = f"{compact} {PLAN_BUDGET}"
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
        write += f" {no_commits(levers)}"
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
        # v2: keep the ask-before-writing directive; only bound the number and shape of questions.
        note = note.replace(
            "such as behaviour, policy, format or scope): up to 4 short choices, recommended first.",
            "such as behaviour, policy, format or scope) and that reading the repo cannot settle: prefer 1 question, "
            "at most 3, each with 2–3 short choices written as label — consequence, recommended first.",
        )
    if "choice_fidelity" in levers:
        note = note.replace("\n2. ", " " + CHOICE_FIDELITY + "\n2. ", 1)
    if "choice_record" in levers:
        note = note.replace("\n2. ", " " + CHOICE_RECORD + "\n2. ", 1)
    if "plan_once" in levers and str(platform).strip().lower() == "mattermost":
        note = note.replace(
            '3. Show the complete plan in your reply, then call',
            '3. If the plan is at most 3,000 characters, show only a 3–5 line summary plus the path in your reply; '
            'the approval prompt contains the full plan. For a longer plan, show the complete plan in your reply. Then call',
        )
    if "plan_once" in levers and reply_plan:
        note = note.replace(f"as Markdown to {target}", "as Markdown in your reply text")
        note = note.replace("2. Write the plan as Markdown in your reply text",
                            "2. Write the complete plan in your reply, in Markdown, between a line <proposed_plan> and a line "
                            "</proposed_plan>, with a blank line after the opening tag and before the closing tag; anything "
                            "else you say goes outside the tags")
        start, end = note.index("3. "), note.index("\n4. ")
        note = note[:start] + (
            '3. In that same response, call plan_mode(action="submit") with no path, on its own '
            '(if plan_mode is not loaded, find it with tool_search "plan_mode"). '
            'The plugin saves only the text between the tags as the plan file; do not write the plan file yourself. '
            'Approval starts implementation in this same turn.'
        ) + note[end:]
    if "bridge_calls" in levers:
        # Every step-3 variant carries this parenthetical; tests assert the swap happened in each one.
        note = note.replace('(if plan_mode is not loaded, find it with tool_search "plan_mode")', BRIDGE_SUBMIT)
    if "lean_craft" in levers and style == "compact" and not plan_skill:
        note += "\n" + (LEAN_CRAFT.split(" Write the plan file ")[0] if reply_plan else LEAN_CRAFT)
    if "test_commands" in levers:
        note += "\n" + TEST_COMMANDS
    if "rename_scope" in levers and "refactor_scope" not in levers:
        note += "\n" + RENAME_SCOPE.format(plans_dir=plans_dir)
    if "test_plan_only" in levers:
        note += "\n" + TEST_PLAN_ONLY
    if "refactor_scope" in levers:
        note += "\n" + REFACTOR_SCOPE.format(plans_dir=plans_dir)
    if "sticky" in levers:
        note += '\nWhile plan mode is on, a request to implement or "just do it" means plan the implementation and submit; do not call blocked tools.'
    if "blocked_tools" in levers:
        note += "\n" + BLOCKED_TOOLS
    if "skill_policy" in levers and not plan_skill:
        note += "\n" + SKILL_POLICY
    if "reentry" in levers and previous_plan:
        note += f"\nPrevious plan: {previous_plan}. Read it; refine it for the same task or start a new file for a different task."
    return note


def light_note(plans_dir, *, levers=frozenset()) -> str:
    base = (f"Plan mode is ON. Plans directory: {plans_dir}. Submit with plan_mode; "
            'do not ask "should I proceed?" in prose.')
    if len(base) > 400:
        return base[:400]
    sentences = [base]
    for lever, sentence in (("bridge_calls", LIGHT_BRIDGE), ("blocked_tools", LIGHT_BLOCKED),
                            ("skill_policy", LIGHT_SKILL)):
        if lever in levers:
            sentences.append(sentence)
    while len("".join(sentences)) > 400:
        sentences.pop()
    return "".join(sentences)


def executing_pointer(state: dict, *, commits=True, levers=frozenset()) -> str:
    revision = state.get("approved_revision")
    rev = f" (rev {revision})" if revision is not None else ""
    result = (
        f"Executing the approved plan {state.get('approved_path')}{rev}. "
        "Keep todo_list statuses current; re-read the plan if your context was compacted."
    ) + ("" if commits else f" {no_commits(levers)}")
    if "todo_inline" in levers:
        result = result.replace("Keep todo_list statuses current;",
                                "If using todo_list, never call it in a response by itself: send updates with your next edit or test call;")
    elif "todo_batch" in levers:
        result = result.replace("Keep todo_list statuses current;", "If using todo_list, batch status updates with the next real tool call;")
    if "lean_craft" in levers:
        result += " " + LEAN_EXECUTION
    if "skill_policy" in levers:
        result += " " + SKILL_EXECUTION
    if "edit_batch" in levers:
        result += " " + EDIT_BATCH
    if "brief_final" in levers:
        result += " " + BRIEF_FINAL
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
