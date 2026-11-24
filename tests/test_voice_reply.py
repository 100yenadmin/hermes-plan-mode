import pytest

from test_plan_mode import _submit, executing_plan, plan_profile, plugin, session_env, submitted_plan

pytestmark = pytest.mark.usefixtures("directive_lean")


@pytest.fixture
def planning(plugin, session_env, tmp_path):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    assert "Plan mode is on" in plugin.command("on voice reply")


@pytest.mark.parametrize("args", [{"text": "hi"}, {"text": "hi", "output_path": None},
                                  {"text": "hi", "output_path": ""}], ids=["absent", "null", "empty"])
def test_planning_allows_default_voice_reply(plugin, planning, args):
    assert plugin.pre_tool_call("text_to_speech", args) is None


@pytest.mark.parametrize("output_path", ["/abs/project/a.mp3", "a.ogg", " ", "~/x.ogg", 123, ["a"], 0, []])
def test_planning_blocks_voice_reply_with_output_path(plugin, planning, output_path):
    result = plugin.pre_tool_call("text_to_speech", {"text": "hi", "output_path": output_path})
    assert result["action"] == "block"
    assert "without output_path" in result["message"]


@pytest.mark.parametrize("args", [{"text": "hi"}, {"text": "hi", "output_path": "a.ogg"}],
                         ids=["default", "custom-path"])
def test_awaiting_approval_keeps_voice_reply_guard(plugin, submitted_plan, args):
    _submit(plugin)
    assert "awaiting approval" in plugin.command("status")

    result = plugin.pre_tool_call("text_to_speech", args)
    if "output_path" in args:
        assert result["action"] == "block"
        assert "without output_path" in result["message"]
    else:
        assert result is None


def test_extra_allowed_tools_can_allow_voice_reply_with_output_path(plugin, planning):
    plugin.ctx.settings["extra_allowed_tools"] = ["text_to_speech"]
    assert plugin.pre_tool_call("text_to_speech", {"text": "hi", "output_path": "a.ogg"}) is None


def test_executing_allows_voice_reply(plugin, executing_plan):
    assert plugin.pre_tool_call("text_to_speech", {"text": "hi"}) is None
    assert plugin.pre_tool_call("text_to_speech", {"text": "hi", "output_path": "a.ogg"}) is None


def test_plan_mode_off_allows_voice_reply(plugin):
    assert plugin.pre_tool_call("text_to_speech", {"text": "hi", "output_path": "a.ogg"}) is None


@pytest.mark.parametrize("args", [None, "x"])
def test_planning_blocks_malformed_voice_reply_args(plugin, planning, args):
    result = plugin.pre_tool_call("text_to_speech", args)
    assert result["action"] == "block"
