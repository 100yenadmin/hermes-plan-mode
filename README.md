# Hermes Plan Mode

## Optional published plan briefs (development candidate)

On a host with `work_presentation` capability 1 and an enabled, topic-scoped
Telegram Experience provider, Plan Mode participates in the same native proposal
journey. After a plan is saved, the agent calls `publish_plan_brief` with a short
audience-safe summary. The host verifies the source file and records its digest;
the agent and user do not calculate hashes or copy task identifiers.

`/planmode approve` then targets that exact published revision. A changed file,
superseded proposal, missing publication or lost scope refuses approval and keeps
enforcement active. It never silently approves the latest other file. Approval
still supplies the instruction for the next turn; it is not proof that work has
started. Native `/plan` without this plugin remains prompt-only planning.

The bridge publishes no raw plan file, transcript or internal comment. Shared
brief audience and action authority belong to the host. Without this optional
capability/provider, existing Plan Mode behavior remains unchanged. This source
integration is not a claim of live Telegram acceptance or stock-host support.

`plan-mode` adds an enforced, per-session planning mode to Hermes. Unlike
Hermes' built-in prompt-only `/plan`, this plugin uses the `pre_tool_call`
policy hook to block mutating Hermes tools dispatched through `pre_tool_call`
until the user approves the plan. Tools that bypass `pre_tool_call` are not
covered; see [Known limitations](#known-limitations).

## Supported surfaces

- **Classic CLI:** enforced on released Hermes (≤ 0.21.4, tag `v2026.9.21`)
  and on newer builds.
- **Messaging gateway (Telegram etc.), TUI, and Desktop:** need a Hermes build
  that includes NousResearch/hermes-agent commits `5943347a2a` (gateway
  plugin-command session binding) and `35fdb4608a` (TUI/Desktop plugin-command
  session binding). Both landed on `main` after `v2026.9.21` and are in no
  release tag yet; `upstream/main@38c289c` (the CI pin) and
  `upstream/main@e5131dc` include them. Earlier builds refuse `/planmode on`
  on these surfaces instead of pretending plan mode is active.

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
- skills and history: `skills_list`, `session_search`, and `skill_view` when
  Hermes reports inline shell off (below);
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
`plan_mode.extra_allowed_tools` (a list of exact tool names) in the profile's
`config.yaml`:

```yaml
plugins:
  entries:
    plan-mode:
      settings:
        plan_mode:
          extra_allowed_tools: [my_read_only_tool]
```

This is an explicit policy override: added tools are trusted as read-only by
the operator.

`skill_view` is allowed only while Hermes itself will not run inline shell for
it. The tool preprocesses SKILL.md through
`agent.skill_preprocessing.preprocess_skill_content` without an explicit
`skills_cfg` (`tools/skills_tool_plugin.py:117` on every Hermes from
`v2026.9.11` through `upstream/main@e5131dc`), so it runs inline shell
only when `load_skills_config().get("inline_shell")` is truthy
(`skills.inline_shell`, default `false`). The hook calls that same public
loader at call time in the same process and context, instead of guessing
config files. `skill_view` stays blocked when `skills.inline_shell` is true,
when the loader cannot be imported, when it raises, or when it returns a
non-dict.

## Session identity and persistence

Beyond the public plugin context, the plugin reads these internal Hermes
seams. Each is imported lazily and wrapped in `try/except`; line citations are
`upstream/main@e5131dc`.

1. `gateway.session_context.get_session_env` (`gateway/session_context.py:173`):
   session identity. If missing, the plugin still loads, `/planmode on`
   refuses with an unsupported-version explanation, and hooks do not block
   because no plan state can be created.
2. `gateway.session_context.session_context_engaged`
   (`gateway/session_context.py:21-23`): whether this process has bound a
   server session. If missing or raising, it is treated as never engaged;
   server processes are then recognised only by Hermes' gateway admission
   marker (item 3), and an unbound server command still refuses.
3. Gateway admission: the `HERMES_GATEWAY_SESSION` environment flag and
   `gateway.run._gateway_runner_ref`, read only if `gateway.run` is already
   imported. If the reference is missing, only the environment flag admits a
   gateway process; a CLI that merely imports gateway code stays a CLI.
4. `agent.runtime_cwd.resolve_agent_cwd` (`agent/runtime_cwd.py:90-92`): the
   turn-scoped workspace. If missing, activation uses an existing absolute
   `TERMINAL_CWD` or the classic CLI process cwd.
5. `hermes_cli.profiles.get_active_profile_name`: the registration profile when
   the plugin's Hermes home is not `profiles/<name>`. If missing or raising,
   the registration profile is unknown: a bound session refuses activation and
   every state-mutating command, and the classic CLI (no bound session
   profile) is unaffected.
6. `agent.skill_preprocessing.load_skills_config`
   (`agent/skill_preprocessing.py:22-31`): whether `skill_view` would run
   inline shell. If missing, raising, or returning a non-dict, `skill_view` is
   blocked.
7. `ctx._manager.home_path` (private `PluginContext._manager`,
   `hermes_cli/plugins.py:235`): the Hermes home the plugin was registered
   from, used to derive the registration profile. If missing, the registration
   profile is unknown and bound sessions refuse every state-mutating command.

The cwd import remains necessary on both target Hermes versions. Their
`gateway.session_context.set_session_vars(..., cwd=...)` stores cwd only in
`agent.runtime_cwd` (`gateway/session_context.py:115-146`); cwd is not one of the
variables exposed by `get_session_env`. Removing that reader would make TUI and
Desktop plans fall back to the backend process directory instead of the session
workspace.

The dependency is intentional:

- `gateway/run_inbound.py:1061-1078` binds `_session_env_scope` around plugin
  command handlers specifically so a handler reading `get_session_env()` sees
  the correct session (`#108698`, commit `5943347a2a`); `_run_plugin_command`
  (`tui_gateway/methods_tools.py:573-585`) does the same for TUI/Desktop
  (commit `35fdb4608a`). Neither is in a release tag through `v2026.9.21`.
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
refuses state-changing commands instead of guessing another tab's UI state;
`status` still reports that the unlinked tab is off. Retry from the owning tab
after Hermes exposes its stable UI identity. This narrow gap remains until
Hermes binds `HERMES_UI_SESSION_ID` around plugin commands or emits a public
rotation mapping.
Gateway sessions without a UI id continue to use the session key. Classic CLI
uses `cli:<pid>`, so conversation compression may rotate `session_id` without
losing plan mode. A nested process that only inherited its parent's session
key, source, platform, and UI id uses its own PID key when Hermes session context
has never been engaged in that process. Activation is refused when that
inherited identity appears inside a gateway/slash-worker process because it
cannot be assigned safely to one session.

Released Hermes through 0.21.4 (`v2026.9.21`) does not bind a TUI/dashboard
session around plugin command handlers. TUI/Desktop session creation sets `HERMES_GATEWAY_SESSION=1`, so even
the first unbound `/planmode on` refuses and names the required Hermes fix.
Its messaging gateway also omits command binding; the gateway-start-only
live-runner reference makes the first `/planmode on` refuse instead of falling
back to a process-wide CLI key. The inherited `HERMES_EXEC_ASK` environment
value alone is not trusted, so nested CLIs remain independent. Gateway, TUI
and Desktop plan mode on released Hermes is therefore unsupported and fails
closed at activation.
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
distinguished, none is cleared. Each active entry records its owning process.
If any session owned by the current process is active and a non-CLI tool call
arrives without a derivable key, the call is blocked fail-closed, except turns
Hermes marks as cron (`HERMES_CRON_SESSION` in the cron scheduler's session
context, which tools cannot set). State left by
another process does not block cron or other bound-but-keyless work.

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
at `upstream/main@e5131dc`). The documented plugin context exposes no
public command-time runtime identifier, so this plugin cannot reliably detect
and refuse that runtime without another private dependency. Plan mode therefore
does **not** enforce Codex-native app-server actions. Use a normal Hermes tool
runtime when enforcement is required.

This plugin performs no network calls, launches no subprocesses, contains no
self-updater, and registers no tools. While plan mode is active, its
`pre_llm_call` hook adds a short plan-mode note to each turn's context.

TUI `/background` and `btw` side agents are rebound under their task id and do
not expose a public parent-session identity to plugin hooks
(`tui_gateway/methods_prompt.py:978` at `upstream/main@e5131dc`). They can
therefore run with full tools even while the parent chat is in plan mode; do
not use those side-agent paths while enforcement is required.

Hermes collects every `pre_tool_call` result before resolving directives, and a
different plugin's later `modify` directive can rewrite arguments after this
plugin checked them (`hermes_cli/plugins.py:1870-1889` at
`upstream/main@e5131dc`). Plan mode cannot
re-validate another plugin's rewritten arguments. Avoid combining it with
argument-rewriting plugins on plan writers.

`upstream/main@e5131dc` resolves the `slash.exec` plugin command handler
before entering the target TUI session's `profile_home` scope
(`tui_gateway/methods_tools.py:933-964`). `_run_plugin_command`
(`tui_gateway/methods_tools.py:573`) does bind `HERMES_SESSION_PROFILE` for the
target session through `_set_session_context` (`tui_gateway/server.py:1269-1296`).
The plugin compares that profile with the Hermes home captured by its registering
plugin manager and refuses activation, `off`, `approve` and `reject` on a mismatch
(`status` stays read-only), so the wrong launch-profile
instance cannot claim enforcement. This refusal remains necessary until upstream
resolves the handler inside the target profile scope.
If two live sessions in one TUI backend share a session key, Hermes binds the
first record's profile (`_session_for_key`, `tui_gateway/server.py:1263` at `upstream/main@e5131dc`), so
the plugin cannot tell them apart; keep session keys unique per profile.

With non-default `compression.in_place: false`, `/planmode on` followed by
`/compress` before any bound turn can rotate the session key before Hermes has
exposed a stable UI id to the plugin. Hermes emits neither a plan-mode hook nor a
public old-to-new command-key mapping at that boundary, so a safe in-plugin copy
would require guessing across tabs. This sequence remains unsupported and is
covered by a strict expected-failure regression; use the default in-place
compression or allow one bound turn before rotating compression.

## Development

```bash
pytest -q
hermes plugins validate .
hermes plugins doctor . --ci
hermes plugins compat .
```

See [`docs/manual-test.md`](docs/manual-test.md) for a surface-by-surface manual
acceptance flow.
