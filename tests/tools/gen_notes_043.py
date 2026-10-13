"""Generate immutable render fixtures from v0.4.3, never from the working copy."""
from datetime import datetime
from itertools import product
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 11, 12, 34, 56)
LEAN_043 = {"lean_craft", "todo_batch", "codex_compact", "sticky", "reentry", "approved_text",
            "light_notes", "question_shape", "bridge_calls", "blocked_tools", "skill_policy",
            "test_commands", "choice_fidelity", "todo_inline"}


# Run by the v0.4.3 tree itself (plugin, render, approval and its own test FakeContext), outside this checkout.
_HINT_SCRIPT = """
import json, sys
sys.path[:0] = [sys.argv[1], sys.argv[1] + "/tests"]
from test_plan_mode import FakeContext
from plan_mode import plugin as pm
out = {"lean": sorted(pm.LEAN_LEVERS), "hints": {}}
for commits in (True, False):
    ctx = FakeContext()
    ctx.settings.update(levers=",".join(sorted(pm.LEAN_LEVERS)), allow_commits=commits)
    plugin = pm.PlanModePlugin(ctx)
    plugin.register()
    out["hints"][str(commits)] = plugin._execute_hint()
print(json.dumps(out))
"""


def old_execute_hints():
    """The gate-path execute hint of v0.4.3 with its lean set, for both commit settings."""
    with tempfile.TemporaryDirectory() as tmp:
        archive = subprocess.check_output(["git", "archive", "v0.4.3", "plan_mode", "tests"], cwd=ROOT)
        subprocess.run(["tar", "-x", "-C", tmp], input=archive, check=True)
        os.mkdir(os.path.join(tmp, "home"))
        env = {k: v for k, v in os.environ.items() if k not in ("PLAN_MODE_LEVERS", "PYTHONPATH")}
        env.update(HERMES_HOME=os.path.join(tmp, "home"), PYTHONDONTWRITEBYTECODE="1")
        out = json.loads(subprocess.check_output([sys.executable, "-c", _HINT_SCRIPT, tmp], cwd=tmp, env=env))
    assert out["lean"] == sorted(LEAN_043), out["lean"]
    return out["hints"]


def generate():
    old = ModuleType("render_043")
    exec(subprocess.check_output(["git", "show", "v0.4.3:plan_mode/render.py"], cwd=ROOT), old.__dict__)
    cases = []
    for style, skill, commits, reply, question, refactor in product(
            ("compact", "core"), ("", "project-plan"), (True, False), (False, True), (False, True), (False, True)):
        levers = (LEAN_043 - {"question_shape"}) | {"plan_once"} | ({"question_shape"} if question else set())
        levers |= {"refactor_scope"} if refactor else set()
        kwargs = dict(plans_dir="/workspace/.hermes/plans", style=style, plan_skill=skill, commits=commits,
                      reply_plan=reply, levers=sorted(levers))
        cases.append(dict(kind="planning_note", kwargs=kwargs, text=old.planning_note(now=NOW, **kwargs)))
    for commits in (True, False):
        kwargs = dict(state={"approved_path": "/plans/fixed.md", "approved_revision": 3},
                      commits=commits, levers=sorted(LEAN_043))
        cases.append(dict(kind="executing_pointer", kwargs=kwargs, text=old.executing_pointer(**kwargs)))
    kwargs = dict(plans_dir="/workspace/.hermes/plans", levers=sorted(LEAN_043))
    cases.append(dict(kind="light_note", kwargs=kwargs, text=old.light_note(**kwargs)))
    for commits, text in old_execute_hints().items():
        cases.append(dict(kind="execute_hint", kwargs=dict(commits=commits == "True", levers=sorted(LEAN_043)), text=text))
    (ROOT / "tests/fixtures/notes_043.json").write_text(json.dumps(cases, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    generate()
