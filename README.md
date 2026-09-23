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
- `/planmode approve` turns enforcement off and injects a one-shot instruction
  on the next turn: `The user approved the plan at <path>. Implement it now.`
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

## Session identity and persistence

The plugin has one sanctioned internal dependency:
`gateway.session_context.get_session_env` (`gateway/session_context.py:173` in
the pinned upstream source). It is imported lazily and wrapped in `try/except`.
If the import is unavailable, the plugin still loads, `/planmode on` refuses
with an unsupported-version explanation, and hooks do not block.

The dependency is intentional:

- `gateway/run_inbound.py:1062-1072` binds `_session_env_scope` around plugin
  command handlers specifically so a handler reading `get_session_env()` sees
  the correct session (`#108698`).
- Hermes propagates the same ContextVar state into tool worker threads; the
  acceptance test exercises the real `_pre_tool_block` entry through
  `tools.thread_context.propagate_context_to_thread`, the helper used by the
  concurrent executor.

The stable key is `sk:<HERMES_SESSION_KEY>` on gateway/TUI/Desktop surfaces.
Classic CLI uses `cli:<pid>`, so conversation compression may rotate
`session_id` without losing plan mode. `on_session_reset` clears the key.
State is stored with the bounded, profile-scoped `ctx.state` facade. A hashed
active-state index lets gateway resets clear a uniquely matching session even
when the reset callback is outside the command's ContextVar scope; raw session
keys and ids are never persisted. If several active sessions cannot be
distinguished, none is cleared. If any session in the process is active and a
non-CLI tool call arrives without a derivable key, the call is blocked
fail-closed; this can intentionally over-block another concurrent session until
its key is available.

## Containment and failure behavior

At activation, the plugin uses an existing absolute `TERMINAL_CWD`, otherwise
`os.getcwd()`, creates `.hermes/plans`, and stores its absolute real path.
Every plan write target must be explicit and absolute. Containment uses
`realpath` plus `commonpath`, so `..`, absolute outside paths, symlink escapes,
and a multi-file patch with any outside target are rejected. Any exception in
the active `pre_tool_call` callback returns a block directive; Hermes otherwise
treats plugin-hook exceptions as fail-open.

## Known limitation: Codex app-server

Hermes' Codex app-server runtime executes native `exec` and `applyPatch`
outside `pre_tool_call` (`agent/transports/codex_app_server_session.py:707-708`
in the pinned upstream source). The documented plugin context exposes no
public command-time runtime identifier, so this plugin cannot reliably detect
and refuse that runtime without another private dependency. Plan mode therefore
does **not** enforce Codex-native app-server actions. Use a normal Hermes tool
runtime when enforcement is required.

This plugin performs no network calls, launches no subprocesses, contains no
self-updater, and registers no tools.

## Development

```bash
pytest -q
hermes plugins validate .
hermes plugins doctor . --ci
hermes plugins compat .
```

See [`docs/manual-test.md`](docs/manual-test.md) for a surface-by-surface manual
acceptance flow.
