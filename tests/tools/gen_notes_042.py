"""Generate immutable render fixtures from v0.4.2 (render.py identical to the measured RC c284e3d), never from the working copy."""
from datetime import datetime
from itertools import product
import json
from pathlib import Path
import subprocess
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 11, 12, 34, 56)
LEAN_042 = {"lean_craft", "todo_batch", "codex_compact", "sticky", "reentry", "approved_text",
            "light_notes", "question_shape", "bridge_calls", "blocked_tools", "skill_policy"}


def generate():
    old = ModuleType("render_042")
    exec(subprocess.check_output(["git", "show", "v0.4.2:plan_mode/render.py"], cwd=ROOT), old.__dict__)
    cases = []
    for style, skill, commits, reply, question, budget in product(
            ("compact", "core"), ("", "project-plan"), (True, False), (False, True), (False, True), (False, True)):
        levers = (LEAN_042 - {"question_shape"}) | ({"question_shape"} if question else set())
        levers |= {"plan_once"} if reply else set()
        levers |= {"plan_budget"} if budget else set()
        kwargs = dict(plans_dir="/workspace/.hermes/plans", style=style, plan_skill=skill, commits=commits,
                      reply_plan=reply, levers=sorted(levers))
        cases.append(dict(kind="planning_note", kwargs=kwargs, text=old.planning_note(now=NOW, **kwargs)))
    for batch in (False, True):
        kwargs = dict(state={"approved_path": "/plans/fixed.md", "approved_revision": 3},
                      levers=sorted((LEAN_042 - {"todo_batch"}) | ({"todo_batch"} if batch else set())))
        cases.append(dict(kind="executing_pointer", kwargs=kwargs, text=old.executing_pointer(**kwargs)))
    kwargs = dict(plans_dir="/workspace/.hermes/plans", levers=sorted(LEAN_042))
    cases.append(dict(kind="light_note", kwargs=kwargs, text=old.light_note(**kwargs)))
    (ROOT / "tests/fixtures/notes_042.json").write_text(json.dumps(cases, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    generate()
