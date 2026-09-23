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
- skills and history: `skills_list`, `skill_view`, `session_search`;
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

`skill_view` is removed from the effective allowlist when the active profile's
`config.yaml` sets `skills.inline_shell: true`, because skill preprocessing can
execute inline-shell snippets in that mode. An unreadable profile config also
blocks `skill_view` fail-closed.

## Session identity and persistence

The plugin has two narrowly scoped internal dependencies required by the WS2
contracts: `gateway.session_context.get_session_env`
(`gateway/session_context.py:173`) for identity, and
`agent.runtime_cwd.resolve_agent_cwd` (`agent/runtime_cwd.py:90-92`) for the
turn-scoped workspace required by fix-round W3. Both are imported lazily and
wrapped in `try/except`. If the identity import is unavailable, the plugin still
loads, `/planmode on` refuses with an unsupported-version explanation, and
hooks do not block. If the cwd resolver is unavailable, activation uses an
existing absolute `TERMINAL_CWD` or the classic CLI process cwd.

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
the first bound turn atomically adopts the command's `sk:<HERMES_SESSION_KEY>`
state into the stable UI key. Gateway sessions without a UI id continue to use
the session key. Classic CLI uses `cli:<pid>`, so conversation compression may
rotate `session_id` without losing plan mode. A nested CLI that merely inherits
the parent's `HERMES_SESSION_KEY` still uses its own PID key.

Hermes 0.21.3 does not bind a TUI/dashboard session around plugin command
handlers. On a loaded server surface, `/planmode on` therefore refuses unless
the command has a real session binding and names the required Hermes fix. The
tool and LLM hooks also treat an active current-process CLI state as plan mode
if a later legacy path derives a session key or no key at all.

`on_session_reset` clears only the derived session or an exact hashed old-session
match; it never guesses based on there being one active session.
`on_session_finalize` clears CLI state, and durable CLI entries whose PID no
longer exists are pruned. State remains bounded and profile-scoped.
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
and a multi-file patch with any outside target are rejected. Any exception in
the active `pre_tool_call` callback returns a block directive; Hermes otherwise
treats plugin-hook exceptions as fail-open.

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

## Development

```bash
pytest -q
hermes plugins validate .
hermes plugins doctor . --ci
hermes plugins compat .
```

See [`docs/manual-test.md`](docs/manual-test.md) for a surface-by-surface manual
acceptance flow.
