"""Plan-profile plumbing: parsing, unchanged overrides and model-text equivalence."""
import pytest

from plan_mode import plugin as pm
from plan_mode import render
from test_levers import LEVER_NAMES, approve_gate, make_plugin, write_plan
from test_plan_mode import session_env


@pytest.mark.parametrize("key", ["plan_profile", "plan_mode.plan_profile"])
@pytest.mark.parametrize("value,expected", [
    ("lean", "lean"), ("classic", "classic"), (" LEAN ", "lean"),
    ("unknown", "lean"), ("", "lean"), (None, "lean"),
])
def test_profile_parsing(make_plugin, key, value, expected):
    plugin = make_plugin(**{key: value})
    assert plugin._plan_profile() == expected


@pytest.mark.parametrize("flat,expected", [("classic", "classic"), ("", "lean"), (None, "lean")])
def test_profile_flat_precedence_matches_style(make_plugin, flat, expected):
    plugin = make_plugin(**{"plan_profile": flat, "plan_mode.plan_profile": "lean",
                            "plan_style": flat, "plan_mode.plan_style": "core"})
    assert plugin._plan_profile() == expected
    assert plugin._plan_style() == ("core" if flat is None else pm._DEFAULT_PLAN_STYLE)


@pytest.mark.parametrize("value", [None, "", "unknown"])
def test_profile_fallback_uses_constant(make_plugin, monkeypatch, value):
    monkeypatch.setattr(pm, "PROFILE_DEFAULT", "lean")
    assert make_plugin(plan_profile=value)._plan_profile() == "lean"


def test_profile_constants_and_selection(make_plugin):
    assert pm.PROFILE_DEFAULT == "lean"
    assert pm.LEAN_LEVERS == frozenset({"lean_craft", "todo_batch", "codex_compact", "sticky",
                                       "reentry", "approved_text", "light_notes", "question_shape"})
    lean = make_plugin()
    lean.ctx.settings.clear()  # genuinely untouched config, including the override key
    assert lean._plan_profile() == "lean" and lean._plan_style() == "compact"
    assert lean._plan_skill() == "" and lean._levers() == pm.LEAN_LEVERS
    lean._levers().clear()  # the selector returns a mutable copy, never the shared profile
    assert len(pm.LEAN_LEVERS) == 8
    assert make_plugin(plan_profile="classic")._levers() == set()


@pytest.mark.parametrize("profile", ["classic", "lean"])
@pytest.mark.parametrize("style", ["core", "compact"])
@pytest.mark.parametrize("skill", ["", "project-plan"])
@pytest.mark.parametrize("blocked", [False, True])
@pytest.mark.parametrize("configured,env,expected", [
    ("sticky", "reentry", {"sticky"}),
    ("", "reentry", {"reentry"}),
    (" \t ", " ReEntry ", {"reentry"}),
    (None, "all", LEVER_NAMES),
    ("ALL", "reentry", LEVER_NAMES),
    ("sticky, reentry unknown", "all", {"sticky", "reentry"}),
    ("unknown", "all", set()),
    ("", "unknown", set()),
    ("", " \t ", None),
    (["sticky"], "all", set()),
])
def test_profile_override_precedence(make_plugin, monkeypatch, profile, style, skill, blocked, configured, env, expected):
    monkeypatch.setattr(pm, "_skill_view_block_reason", lambda: "blocked" if blocked else None)
    monkeypatch.setenv("PLAN_MODE_LEVERS", env)
    plugin = make_plugin(configured, plan_profile=profile, plan_style=style, plan_skill=skill)
    if expected is None:
        expected = pm.LEAN_LEVERS if profile == "lean" and style == "compact" and (not skill or blocked) else set()
    assert plugin._levers() == expected
    plugin.ctx.settings = {"plan_mode.levers": "sticky", "plan_profile": profile,
                           "plan_style": style, "plan_skill": skill}
    assert plugin._levers() == {"sticky"}


@pytest.mark.parametrize("style", ["core", "compact"])
@pytest.mark.parametrize("commits", [False, True])
@pytest.mark.parametrize("skill", ["", "project-plan"])
def test_lean_model_text_equals_explicit_levers(make_plugin, monkeypatch, style, commits, skill):
    monkeypatch.setattr(pm, "_skill_view_block_reason", lambda: None)
    monkeypatch.setattr(render, "plan_file_stamp", lambda now=None: "2026-10-11_123456")
    selected = pm.LEAN_LEVERS if style == "compact" and not skill else set()
    strings = []
    for settings in ({"plan_profile": "lean"},
                     {"plan_profile": "classic", "levers": ",".join(sorted(selected))}):
        plugin = make_plugin(plan_style=style, allow_commits=commits, plan_skill=skill, **settings)
        assert plugin._levers() == selected
        plugin.command("on parser")
        note = plugin.pre_llm_call()["context"]
        path = write_plan(plugin)
        result = approve_gate(plugin, path)
        pointer = plugin.pre_llm_call()["context"]
        strings.append((note, result["message"], pointer, plugin._execute_hint()))
        assert ("same response" in result["message"]) == bool(selected)
        assert ("batch status updates with the next real tool call" in pointer) == bool(selected)
        if style == "compact" and not skill:
            assert "every consumer" in note
    assert strings[0] == strings[1]


@pytest.mark.parametrize("style,skill,blocked,expected", [
    ("core", "", False, set()), ("compact", "project-plan", False, set()),
    ("compact", "project-plan", True, pm.LEAN_LEVERS),
])
def test_profile_default_style_and_effective_skill(make_plugin, monkeypatch, style, skill, blocked, expected):
    import json
    from pathlib import Path
    monkeypatch.setattr(pm, "_skill_view_block_reason", lambda: "inline shell enabled" if blocked else None)
    plugin = make_plugin(plan_style=style, plan_skill=skill)
    assert plugin._plan_profile() == "lean"
    assert plugin._plan_skill() == (skill if not blocked else "")
    assert plugin._levers() == expected
    if style == "core":
        golden = json.loads((Path(__file__).parent / "fixtures/default_off_035.json").read_text())
        monkeypatch.setattr(render, "plan_file_stamp", lambda now=None: "2026-10-11_123456")
        state = {"plans_dir": "/workspace/.hermes/plans"}
        for commits in (False, True):
            plugin.ctx.settings["allow_commits"] = commits
            assert plugin._planning_note(state) == golden["notes"][f"core:{commits}:"]
    elif blocked:
        plugin.command("on")
        assert "skill_view" not in plugin.pre_llm_call()["context"]
        assert plugin.pre_tool_call("skill_view", {"name": skill})["action"] == "block"


@pytest.mark.parametrize("profile,configured,env,label", [
    (None, "", "", "lean"),
    ("classic", "", "", "classic"), ("lean", "", "", "lean"),
    ("lean", "sticky", "", "lean (custom levers)"),
    ("classic", "", "all", "classic (custom levers)"),
    ("lean", "unknown", "", "lean (custom levers)"),
    ("lean", " \t ", " \t ", "lean"),
])
def test_profile_status_adds_only_one_line(make_plugin, monkeypatch, profile, configured, env, label):
    monkeypatch.setenv("PLAN_MODE_LEVERS", env)
    plugin = make_plugin(configured, **({"plan_profile": profile} if profile is not None else {}))
    response = plugin.command("status")
    line = f"Plan profile: {label}"
    assert response.splitlines().count(line) == 1
    assert "\n".join(text for text in response.splitlines() if text != line) == (
        "Plan mode: off\nEntered at: not set\nPhase: off\nPlans directory: not set\nPlan files:\n- none")
    state = {"active": True, "entered_at": "fixed", "plans_dir": "/plans", "submission": {
        "status": "pending", "revision": 3}}
    response = plugin._status_text(state, ["/plans/fixed.md"])
    assert "\n".join(text for text in response.splitlines() if text != line) == (
        "Plan mode: on\nEntered at: fixed\nPhase: awaiting approval (rev 3)\n"
        "Last submission: rev 3 pending\nPlans directory: /plans\nPlan files:\n- /plans/fixed.md")


def test_profile_manifest_select_and_validation(tmp_path, monkeypatch):
    from pathlib import Path
    from hermes_cli import config, plugins, plugins_settings

    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(config, "load_config_readonly", lambda: {})
    fields = plugins_settings.plugin_settings_fields("plan-mode", root)
    field = next(field for field in fields if field["key"] == "plan_profile")
    assert field["type"] == "enum" and field["choices"] == ["lean", "classic"]
    assert field["label"] == "Plan profile"
    assert field["value"] == field["default"] == pm.PROFILE_DEFAULT == "lean"
    schema = plugins_settings._manifest_config_schema(root)
    assert "levers" not in schema
    for value in ("classic", "lean"):
        assert plugins.validate_config_schema("plan-mode", schema, {"plan_profile": value}) == []
    assert plugins.validate_config_schema("plan-mode", schema, {}) == []
    assert plugins.validate_config_schema("plan-mode", schema, {"plan_profile": 42})
    monkeypatch.setattr(plugins_settings, "save_plugin_setting", lambda *_: None)
    for value in ("classic", "lean"):
        assert plugins_settings.save_plugin_settings("plan-mode", root, {"plan_profile": value}) == ["plan_profile"]
    with pytest.raises(ValueError, match="declared choices"):
        plugins_settings.save_plugin_settings("plan-mode", root, {"plan_profile": "unknown"})
