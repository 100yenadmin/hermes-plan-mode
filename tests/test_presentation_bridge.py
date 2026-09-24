"""Optional bridge preserves approval identity; host integration is checked separately."""
import json
from types import SimpleNamespace

from test_plan_mode import FakeContext, session_env
from plan_mode.plugin import PlanModePlugin


class Service:
    def __init__(self):
        self.ref = SimpleNamespace(proposal_id='proposal', incarnation=1, revision=1, source_sha256='a' * 64)
        self.modes = []
        self.transitions = []
        self.changed = False

    def set_plan_mode(self, active):
        self.modes.append(active)

    def current_proposal(self):
        return self.ref

    def transition_proposal(self, ref, **kwargs):
        if self.changed:
            raise ValueError('source changed')
        self.transitions.append(kwargs)
        self.ref = SimpleNamespace(**{**vars(ref), 'revision': ref.revision + 1})
        return self.ref


def bound_plugin(tmp_path, monkeypatch, session_env):
    session_env['TERMINAL_CWD'] = str(tmp_path)
    monkeypatch.chdir(tmp_path)
    ctx = FakeContext()
    service = Service()
    ctx.work_presentation_capability = 1
    ctx.get_work_presentation = lambda: service
    plugin = PlanModePlugin(ctx)
    plugin.register()
    assert 'Plan mode is on' in plugin.command('on compare hotels')
    path = tmp_path / '.hermes/plans/research.md'
    assert plugin.pre_tool_call('write_file', {'path': str(path), 'content': '# Plan'}) is None
    path.write_text('# Plan')
    args = {'source_path': str(path), 'presentation': {'summary': 'Compare three hotels'}}
    assert plugin.pre_tool_call('publish_plan_brief', args) is None
    plugin.post_tool_call('publish_plan_brief', args, json.dumps({'ok': True, **vars(service.ref)}))
    return plugin, service, path


def test_bound_approval_uses_published_revision_and_waits_for_next_turn(tmp_path, monkeypatch, session_env):
    plugin, service, path = bound_plugin(tmp_path, monkeypatch, session_env)
    response = plugin.command('approve')
    assert 'Next turn' in response
    assert str(path) in response
    assert service.transitions[0]['expected_revision'] == 1
    assert service.transitions[0]['source_sha256'] == 'a' * 64
    assert service.modes == [True, False]
    assert 'Plan mode: off' in plugin.command('status')


def test_changed_source_refuses_approval_and_keeps_enforcement(tmp_path, monkeypatch, session_env):
    plugin, service, _ = bound_plugin(tmp_path, monkeypatch, session_env)
    service.changed = True
    assert 'refused' in plugin.command('approve')
    assert 'Plan mode: on' in plugin.command('status')
    assert service.transitions == []
    assert service.modes == [True]
