"""Plan-profile plumbing: parsing, unchanged overrides and model-text equivalence."""
import pytest

from plan_mode import plugin as pm
from plan_mode import render
from test_levers import LEVER_NAMES, approve_gate, make_plugin, write_plan
from test_plan_mode import session_env


@pytest.mark.parametrize("key", ["plan_profile", "plan_mode.plan_profile"])
@pytest.mark.parametrize("value,expected", [
    ("lean", "lean"), ("classic", "classic"), (" LEAN ", "lean"),
    ("unknown", "classic"), ("", "classic"), (None, "classic"),
])
def test_profile_parsing(make_plugin, key, value, expected):
    plugin = make_plugin(**{key: value})
    assert plugin._plan_profile() == expected


@pytest.mark.parametrize("flat,expected", [("classic", "classic"), ("", "classic"), (None, "lean")])
def test_profile_flat_precedence_matches_style(make_plugin, flat, expected):
    plugin = make_plugin(**{"plan_profile": flat, "plan_mode.plan_profile": "lean",
                            "plan_style": flat, "plan_mode.plan_style": "core"})
    assert plugin._plan_profile() == expected
    assert plugin._plan_style() == ("core" if flat is None else pm._DEFAULT_PLAN_STYLE)


@pytest.mark.parametrize("value", [None, "", "unknown"])
def test_profile_fallback_uses_constant(make_plugin, monkeypatch, value):
    monkeypatch.setattr(pm, "PROFILE_DEFAULT", "lean")
    assert make_plugin(plan_profile=value)._plan_profile() == "lean"


def test_profile_constants_and_selection(make_plugin, monkeypatch):
    assert pm.PROFILE_DEFAULT == "classic"
    assert pm.LEAN_LEVERS == frozenset()
    sample = frozenset({"codex_compact", "todo_batch"})
    monkeypatch.setattr(pm, "LEAN_LEVERS", sample)
    lean = make_plugin(plan_profile="lean")
    assert lean._levers() == sample
    lean._levers().clear()  # the selector returns a mutable copy, never the shared profile
    assert pm.LEAN_LEVERS == sample
    assert make_plugin(plan_profile="classic")._levers() == set()


@pytest.mark.parametrize("profile", ["classic", "lean"])
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
def test_profile_override_precedence(make_plugin, monkeypatch, profile, configured, env, expected):
    sample = frozenset({"codex_compact", "todo_batch"})
    monkeypatch.setattr(pm, "LEAN_LEVERS", sample)
    monkeypatch.setenv("PLAN_MODE_LEVERS", env)
    plugin = make_plugin(configured, plan_profile=profile)
    if expected is None:
        expected = sample if profile == "lean" else set()
    assert plugin._levers() == expected
    plugin.ctx.settings = {"plan_mode.levers": "sticky", "plan_profile": profile}
    assert plugin._levers() == {"sticky"}


@pytest.mark.parametrize("style", ["core", "compact"])
@pytest.mark.parametrize("commits", [False, True])
@pytest.mark.parametrize("skill", ["", "project-plan"])
def test_lean_model_text_equals_explicit_levers(make_plugin, monkeypatch, style, commits, skill):
    sample = frozenset({"codex_compact", "todo_batch"})
    monkeypatch.setattr(pm, "LEAN_LEVERS", sample)
    monkeypatch.setattr(render, "plan_file_stamp", lambda now=None: "2026-10-11_123456")
    strings = []
    for settings in ({"plan_profile": "lean"},
                     {"plan_profile": "classic", "levers": "codex_compact,todo_batch"}):
        plugin = make_plugin(plan_style=style, allow_commits=commits, plan_skill=skill, **settings)
        assert plugin._levers() == sample
        path = write_plan(plugin)
        note = plugin.pre_llm_call()["context"]
        result = approve_gate(plugin, path)
        pointer = plugin.pre_llm_call()["context"]
        strings.append((note, result["message"], pointer, plugin._execute_hint()))
        assert "same response" in result["message"]
        assert "batch status updates with the next real tool call" in pointer
        if style == "compact" and not skill:
            assert "every consumer" in note
    assert strings[0] == strings[1]


@pytest.mark.parametrize("profile,configured,env,label", [
    ("classic", "", "", "classic"), ("lean", "", "", "lean"),
    ("lean", "sticky", "", "lean (custom levers)"),
    ("classic", "", "all", "classic (custom levers)"),
    ("lean", "unknown", "", "lean (custom levers)"),
    ("lean", " \t ", " \t ", "lean"),
])
def test_profile_status_adds_only_one_line(make_plugin, monkeypatch, profile, configured, env, label):
    monkeypatch.setenv("PLAN_MODE_LEVERS", env)
    plugin = make_plugin(configured, plan_profile=profile)
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
    assert field["value"] == field["default"] == pm.PROFILE_DEFAULT == "classic"
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
