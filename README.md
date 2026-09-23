# Hermes Plan Mode

`plan-mode` adds an enforced, per-session planning mode to Hermes CLI, gateway,
and TUI sessions. Unlike Hermes' built-in prompt-only `/plan`, this plugin uses
the `pre_tool_call` policy hook to block implementation tools until the user
approves the plan.

## Commands

- `/planmode on [task]` fixes an absolute plan directory for this session and
  enables enforcement.
- `/planmode status` reports the mode, entry time, fixed directory, and plan
  Markdown files written there.
- `/planmode approve [file]` turns enforcement off and injects a one-shot
  instruction on the next turn: `The user approved the plan at <path>.
  Implement it now.` Without a file it selects the newest plan write allowed
  for this session; an explicit file must also belong to this session.
- `/planmode reject [feedback]` keeps enforcement on and injects the feedback
  once on the next turn.
- `/planmode off` turns enforcement off without an approval instruction.

Plans use this convention:

```text
<session cwd>/.hermes/plans/YYYY-MM-DD_HHMMSS-<slug>.md
```

The command prints the exact absolute directory. Plan writes must use absolute
paths under that directory; relative paths are deliberately rejected.

## What is allowed

The built-in allowlist is derived from Hermes' registered tools at
`upstream/main@38c289c0146e`:

- file exploration: `read_file`, `search_files`;
- web research: `web_search`, `web_extract`;
- skills and history: `skills_list`, `session_search`;
- planning/user input: `todo_list`, `clarify`;
- analysis-only media/browser reads: `vision_analyze`, `video_analyze`,
  `browser_snapshot`, `browser_get_images`, `browser_vision`;
- local read-only UI inspection: `read_terminal`, `read_window_below`;
- `write_file` and `patch` only when every explicit target is an absolute path
  whose real path stays inside the fixed plans directory.

Hermes' `memory` tool is not allowed because its current registry surface has
only `add`, `replace`, `remove`, and batch mutations; there is no separable
read-only operation. Terminal, code kernels, delegation, messaging, MCP and
connector tools, browser mutations, cron/kanban mutations, and every unknown
tool are blocked.

Administrators may extend the allowlist with the plugin setting
`plan_mode.extra_allowed_tools` (a list of exact tool names). This is an
explicit policy override: added tools are trusted as read-only by the operator.

`skill_view` is always blocked in plan mode. Skill preprocessing can execute
inline-shell snippets, and a plugin cannot reliably prove which profile config
governs a project-plugin call, so config-file guessing is not a safe allowlist
gate.

## Session identity and persistence

The plugin has two narrowly scoped internal dependencies required by the WS2
contracts: `gateway.session_context.get_session_env` plus
`session_context_engaged` (`gateway/session_context.py:21-23,173`) for identity,
and `agent.runtime_cwd.resolve_agent_cwd` (`agent/runtime_cwd.py:90-92`) for the
turn-scoped workspace required by fix-round W3. Both modules are imported lazily
and wrapped in `try/except`. If the identity import is unavailable, the plugin
still loads, `/planmode on` refuses with an unsupported-version explanation,
and hooks do not block. If the cwd resolver is unavailable, activation uses an
existing absolute `TERMINAL_CWD` or the classic CLI process cwd.

The cwd import remains necessary on both target Hermes versions. Their
`gateway.session_context.set_session_vars(..., cwd=...)` stores cwd only in
`agent.runtime_cwd` (`gateway/session_context.py:115-144`); cwd is not one of the
variables exposed by `get_session_env`. Removing that reader would make TUI and
Desktop plans fall back to the backend process directory instead of the session
workspace.

The dependency is intentional:

- `gateway/run_inbound.py:1062-1072` binds `_session_env_scope` around plugin
  command handlers specifically so a handler reading `get_session_env()` sees
  the correct session (`#108698`).
- Hermes propagates the same ContextVar state into tool worker threads; the
  acceptance test exercises the real `_pre_tool_block` entry through
  `tools.thread_context.propagate_context_to_thread`, the helper used by the
  concurrent executor.

The stable key is `ui:<HERMES_UI_SESSION_ID>` when a TUI/Desktop turn binds its
tab id. Because both target versions omit that id on the plugin-command path,
the first bound turn adopts the command's `sk:<HERMES_SESSION_KEY>` state into
the stable UI key without deleting the source state. Each bound turn records
the current hashed `sk:` storage key in the UI state, so `status`, `approve`,
`reject`, and `off` resolve the same state before and after session-key rotation.
Re-enabling an already linked state preserves those aliases. If a command using
a newly rotated key arrives before any bound hook has recorded it, the plugin
refuses the command instead of guessing another tab's UI state; submit one
ordinary turn in that tab and retry. This narrow gap remains until Hermes binds
`HERMES_UI_SESSION_ID` around plugin commands or emits a public rotation mapping.
Gateway sessions without a UI id continue to use the session key. Classic CLI
uses `cli:<pid>`, so conversation compression may rotate `session_id` without
losing plan mode. A nested process that only inherited its parent's session
key, source, platform, and UI id uses its own PID key when Hermes session context
has never been engaged in that process.

Hermes 0.21.3 does not bind a TUI/dashboard session around plugin command
handlers. TUI/Desktop session creation sets `HERMES_GATEWAY_SESSION=1`, so even
the first unbound `/planmode on` refuses and names the required Hermes fix.
Importing `gateway.run` alone is not treated as a server signal, so normal CLI
remains usable after every chat-turn import. The tool and LLM hooks
also treat an active current-process CLI state as plan mode if a later legacy
path derives a session key or no key at all.

`on_session_reset` clears only the derived session or an exact hashed old-session
match; it never guesses based on there being one active session.
`on_session_finalize` clears only this process's `cli:<pid>` state; gateway,
Desktop idle/LRU, disconnect, and shutdown finalization never clear session
state. Durable CLI entries whose PID no longer exists are pruned on POSIX.
Windows keeps them until explicit CLI finalization because `os.kill(pid, 0)` is
not a non-destructive liveness probe there. State remains bounded and
profile-scoped.
State is stored with the bounded, profile-scoped `ctx.state` facade. A hashed
active-state index lets gateway resets clear a uniquely matching session even
when the reset callback is outside the command's ContextVar scope; raw session
keys and ids are never persisted. If several active sessions cannot be
distinguished, none is cleared. If any session in the process is active and a
non-CLI tool call arrives without a derivable key, the call is blocked
fail-closed; this can intentionally over-block another concurrent session until
its key is available.

## Containment and failure behavior

At activation, the plugin uses Hermes' turn-scoped cwd resolver—the same cwd
bound by TUI/gateway `_set_session_context(..., cwd=...)`—then an existing
absolute `TERMINAL_CWD`, and only uses `os.getcwd()` for classic CLI fallback.
It refuses cleanly if the directory cannot be created. It also refuses when
either `.hermes` or `.hermes/plans` is a symlink or the final real path differs
from `<real session cwd>/.hermes/plans`.
Every plan write target must be explicit and absolute. Containment uses
`realpath` plus `commonpath`, so `..`, absolute outside paths, symlink escapes,
and a multi-file patch with any outside target are rejected. The hook also
revalidates `.hermes` and `.hermes/plans` immediately before allowing each
writer. A narrow time-of-check/time-of-use race remains because the plugin does
not own Hermes' eventual file-open operation; do not let side agents or other
processes mutate the plan root during plan mode. Any exception in the active
`pre_tool_call` callback returns a block directive; Hermes otherwise treats
plugin-hook exceptions as fail-open.

## Known limitations

Hermes' Codex app-server runtime executes native `exec` and `applyPatch`
outside `pre_tool_call` (`agent/transports/codex_app_server_session.py:707-708`
in the pinned upstream source). The documented plugin context exposes no
public command-time runtime identifier, so this plugin cannot reliably detect
and refuse that runtime without another private dependency. Plan mode therefore
does **not** enforce Codex-native app-server actions. Use a normal Hermes tool
runtime when enforcement is required.

This plugin performs no network calls, launches no subprocesses, contains no
self-updater, and registers no tools.

TUI `/background` and `btw` side agents are rebound under their task id and do
not expose a public parent-session identity to plugin hooks
(`tui_gateway/methods_prompt.py:978` in the pinned upstream source). They can
therefore run with full tools even while the parent chat is in plan mode; do
not use those side-agent paths while enforcement is required.

Hermes collects every `pre_tool_call` result before resolving directives, and a
different plugin's later `modify` directive can rewrite arguments after this
plugin checked them (`hermes_cli/plugins.py:1870-1889`). Plan mode cannot
re-validate another plugin's rewritten arguments. Avoid combining it with
argument-rewriting plugins on plan writers.

Pinned upstream Hermes 0.21.4 resolves the `slash.exec` plugin command handler
before entering the target TUI session's `profile_home` scope
(`tui_gateway/methods_tools.py:933-964`). In a multi-profile dashboard, a
profile-B slash command can therefore reach the launch profile's plugin manager
and plan-mode state. The real regression test is strict-xfail until upstream
routes handler resolution under the session profile. The plugin cannot repair
this because the wrong manager and state facade are selected before its handler
runs.

## Development

```bash
pytest -q
hermes plugins validate .
hermes plugins doctor . --ci
hermes plugins compat .
```

See [`docs/manual-test.md`](docs/manual-test.md) for a surface-by-surface manual
acceptance flow.
