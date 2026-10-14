"""SPEC-039: conservative, linear extraction against both pinned host parsers."""
import importlib.util
import random
import re
import signal
import sys
from pathlib import Path
from time import perf_counter
from types import ModuleType

import pytest

from plan_mode.plugin import _patch_targets
from test_plan_mode import plugin, plan_profile, session_env


HOSTS = Path("/Users/m1/Codex/session-notes/2026-10-14/plan-mode-046-resume/artifacts/hosts")
FORMS = ("Update", "Add", "Delete", "Move")
# Frozen e884710 oracle; keep inputs small because its Move regex is cubic.
BASE_FILE = re.compile(r"^\*\*\*\s*(?:Update|Add|Delete)\s+File:\s*(.+)$", re.MULTILINE)
BASE_MOVE = re.compile(r"^\*\*\*\s*Move\s+File:\s*(.+?)\s*->\s*(.+)$", re.MULTILINE)


def targets(patch):
    return set(_patch_targets({"mode": "patch", "patch": patch}) or [])


def base_targets(patch):
    result = {m.group(1).strip() for m in BASE_FILE.finditer(patch)}
    result.update(g.strip() for m in BASE_MOVE.finditer(patch) for g in m.groups())
    return result


def generated_patches():
    rng = random.Random(39)
    spaces = ("", " ", "\t", "\r", "\n", "\r\n", "\u2003", " \n\t ")
    paths = ("", "a.md", "plans/日本語.md", "é.md", "a b", "->", "a -> b", "a ->", "#", "***")
    for _ in range(6000):
        lines = []
        for _ in range(rng.randint(1, 4)):
            kind = rng.choice(FORMS)
            line = (rng.choice(("", "", "+", " ")) + "***" + rng.choice(spaces)
                    + kind + rng.choice(spaces) + "File:" + rng.choice(spaces) + rng.choice(paths))
            if kind == "Move":
                line += rng.choice(("", "->", " -> ", "\n->\n", " -> b -> ")) + rng.choice(paths)
            lines.append(line)
        yield rng.choice(("\n", "\r\n")).join(lines) + rng.choice(spaces)


def test_seeded_differential_never_loses_base_targets():
    count = 0
    for patch in generated_patches():
        assert base_targets(patch) <= targets(patch), repr(patch)
        count += 1
    assert count == 6000
    print(f"differential inputs={count}, seed=39")


@pytest.mark.parametrize("patch", [
    "*** Add File:\nnext.md",
    "***\nUpdate\nFile:\r\nnext.md",
    "*** Move File: a\n -> b",
    "*** Move File: a ->\r\n b",
    "*** Move File:  -> b",
    "*** Move File: -> a -> b",
    "*** Move File: a -> b ->",
    "*** Move File: -> a ->\n\n",
    "*** Delete File: \t\r",
])
def test_legacy_newline_and_arrow_targets_are_preserved(patch):
    assert base_targets(patch) <= targets(patch)


@pytest.mark.parametrize("kind", FORMS)
@pytest.mark.parametrize("character", [" ", "\n", "*", "#", "\t", "\r"])
@pytest.mark.parametrize("position", ["after_header", "after_stars", "after_kind", "after_arrow"])
def test_one_mb_adversarial_under_50_ms(kind, character, position):
    run = character * 1_000_000
    prefix = f"*** {kind} File:"
    if position == "after_stars":
        patch = f"***{run}{kind} File: a -> b"
    elif position == "after_kind":
        patch = f"*** {kind}{run}File: a -> b"
    elif position == "after_arrow":
        patch = prefix + " a ->" + run
    else:
        patch = prefix + run

    def deadline(_signum, _frame):
        raise AssertionError("1 MB extraction exceeded 50 ms")

    previous = signal.signal(signal.SIGALRM, deadline)
    started = perf_counter()
    try:
        signal.setitimer(signal.ITIMER_REAL, 0.05)
        _patch_targets({"mode": "patch", "patch": patch})
        elapsed = perf_counter() - started
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
    assert elapsed < 0.05
    print(f"timing {kind}/{position}/{character!r}: {elapsed * 1000:.3f} ms")


@pytest.fixture(params=["v0.21.6", "main-7bc05085"])
def host_parser(request, monkeypatch):
    # Load only the parser and its pure dataclasses, with no host runtime/config.
    directory = HOSTS / request.param / "tools"
    package = ModuleType("tools")
    package.__path__ = [str(directory)]
    monkeypatch.setitem(sys.modules, "tools", package)
    for name, filename in (("tools.file_operations_common", "file_operations_common.py"),
                           ("spec039_patch_parser", "patch_parser.py")):
        spec = importlib.util.spec_from_file_location(name, directory / filename)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
    return module.parse_v4a_patch


def host_patch(kind, source, destination, separator, ending):
    header = f"***{separator}{kind}{separator or ' '}File:{separator}{source}"
    if kind == "Move":
        header += f" -> {destination}"
    body = ["*** Begin Patch", header]
    if kind == "Update":
        body += ["@@", "-old", "+new"]
    elif kind == "Add":
        body += ["+new"]
    return ending.join(body + ["*** End Patch"])


@pytest.mark.parametrize("kind", FORMS)
@pytest.mark.parametrize("separator", ["", " ", "\t", "\u2003"])
@pytest.mark.parametrize("ending", ["\n", "\r\n"])
def test_every_host_header_target(host_parser, kind, separator, ending):
    patch = host_patch(kind, "plans/日本語.md", "outside.md -> suffix", separator, ending)
    operations, error = host_parser(patch)
    assert error is None and len(operations) == 1
    expected = {op.file_path for op in operations}
    expected.update(op.new_path for op in operations if op.new_path is not None)
    assert expected <= targets(patch)


@pytest.mark.parametrize("kind", FORMS)
@pytest.mark.parametrize("separator", ["", " ", "\t", "\u2003"])
def test_planning_blocks_every_host_write_form(host_parser, plugin, session_env, tmp_path, kind, separator):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    plugin.command("on")
    safe = tmp_path / ".hermes" / "plans" / "source.md"
    outside = tmp_path / "outside.md"
    patch = host_patch(kind, str(safe if kind == "Move" else outside), str(outside), separator, "\r\n")
    operations, error = host_parser(patch)
    assert error is None and len(operations) == 1
    assert plugin.pre_tool_call("patch", {"mode": "patch", "patch": patch})["action"] == "block"


def test_move_to_is_not_a_host_move_header(host_parser):
    patch = "*** Update File: source.md\n*** Move to: outside.md\n@@\n-old\n+new"
    operations, error = host_parser(patch)
    assert error is None
    assert [(op.operation.value, op.file_path, op.new_path) for op in operations] == [("update", "source.md", None)]
