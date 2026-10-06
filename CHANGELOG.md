# Changelog

## 0.3.4 - 2026-10-06

- When Hermes approves a submit automatically (yolo or `approvals.mode: off`), ask for human approval through the
  core `clarify` tool with the exact choices **Approve plan rev N** and **Keep planning**. Approval comes only
  from that question's correlated tool result, or typed `/planmode approve`; it starts implementation in the same
  turn. Keep planning and free text become review feedback. Denied, cancelled or unanswered human approval
  prompts do not trigger this fallback.
- Bind the question to its nonce, tool call, activation and unchanged plan digest. Block altered tagged questions
  and parallel approval questions; leave non-answers awaiting so the question can be re-asked. Typed commands,
  resubmission and session cleanup clear the question. Status and turn notes show approval asked in chat.

## 0.3.3 - 2026-10-06

- Fix: the short approval summary on Telegram, Slack and Discord lists the plan's steps. It takes them from the
  "Step-by-step tasks" section that core `/plan` asks for, or another steps/tasks section; then "Step N" headings;
  then numbered items. Before, it took the first headings and bullets in the file. A live Telegram run showed
  "1. Goal 2. Current context / assumptions 3. Workspace root: …" instead of the steps. A "Plan (v2):" prefix is
  also dropped from the title.
- The summary skips label sections ("Tests", "Risks", "Implementation notes") and anything under them, reads a
  "changes" section as work, keeps task headings (bullets under them are details), and never reads fenced or indented
  code as a heading. It is linear in the number of headings.
- Fix: `footer: off` in config.yaml now turns the chat footer off. Hermes reads config.yaml as YAML 1.1, where a bare
  `off` is the boolean false, and the plugin only compared against the string "off".
- Settings ▸ Plugins: plugin.yaml declares a `config_schema` for `enforce_builtin_plan`, `agent_hint`, `footer` and
  `extra_allowed_tools`, so Hermes Desktop shows a form for them (inline under Capabilities ▸ Plugins on v2026.9.24).
  The keys are flat, and a flat key now wins over the nested `plan_mode.<key>` layout, which is still read as a
  fallback. The README leads with the form.
- Updating plan-mode no longer makes Hermes re-sync its dependencies on the next start. The plugin has no Python
  dependencies, but its `pyproject.toml` made it a member of Hermes' package-manager workspace, so every update
  changed the member set and the next gateway or CLI start re-synced dependencies. On Hermes builds before
  NousResearch/hermes-agent#131001 that start also rebuilt the TUI, web UI and Desktop app (about 4 minutes). The file is gone
  (test settings moved to `pytest.ini`), so plan-mode is a plain plugin. The first start after this update still
  syncs once, because the member set changes one last time.

## 0.3.2 - 2026-10-06

- Fix: the TUI/Desktop approval card now shows a one-line prompt ("Plan rev N (file) …approve to start
  implementing, deny to keep planning"), like the classic CLI panel. The Ink card prints the approval text as its
  title without a line limit. In a live TUI run, the full plan pushed the approve/deny choices below the visible area
  of a normal terminal. The full plan is still the agent's reply just above the card, and `/planmode show` still
  prints it.

## 0.3.1 - 2026-10-06

- Fix: enabling plan-mode in two profiles of one Hermes install no longer breaks Hermes' dependency sync. The
  plugin's `pyproject.toml` declared a setuptools build backend, so Hermes' package manager kept its project name
  (`hermes-plan-mode`) for every copy. Two enabled copies were then two workspace members with one name, and
  `uv lock` refused the workspace: source-update completion failed on every start. The plugin has no dependencies
  and was never built, so the build backend is gone. Hermes now treats each copy as a metadata-only member with a
  per-profile name. Nothing else changes.

## 0.3.0 - 2026-10-05

- Add `plan_mode(action="submit", path?, summary?)`. It asks the user to approve the plan through Hermes' own
  approval prompt (CLI panel, TUI/Desktop card, gateway buttons, or `/approve` text) under a rule key unique to the
  plan revision. A prompt approval counts only when `post_approval_response` reports once, session or always,
  without a cancel, for that exact key and tool call (typed `/planmode approve` is the other human path). On approval the same tool call turns plan mode off and tells
  the agent to implement now and mirror the steps into `todo_list` (naming the exact `todo_list(todos=[...])` call).
  The tool still cannot approve on its own.
- A submitted plan belongs to the user: after a submit, the agent can no longer turn plan mode off, even when it
  entered plan mode itself. The agent-entered turn note now allows `off` only before submitting.
- With yolo or `approvals.mode: off` Hermes approves without asking and fires no hook: plan mode stays on and the
  agent is told to ask for `/planmode approve`. A host that ignores the approval directive degrades the same way.
- Deny keeps plan mode on and adds a one-shot note that a denial is review feedback: revise and submit a complete
  new revision. The deny reason exists only on gateway text `/deny <reason>`. A timed-out or withdrawn prompt leaves
  the revision awaiting approval.
- Plans are hashed at submit. An edit before the approval lands, or before a typed approve without a file, refuses
  the approval. A second submit while a prompt is open is blocked. Revisions keep counting across activations.
- Approval text by platform: one line on the classic CLI, whose panel does not wrap multi-line text (the plan is
  printed just above it); a ≤250-char summary (title + up to 6 step titles) on Telegram, Slack and Discord; the
  full plan capped at about 1000 chars on WhatsApp Cloud (its card limit) and at about 3500 chars everywhere else.
- "Always" behaves like once for plans, but Hermes core writes a `plugin_rule:plan-mode:…` entry to
  `command_allowlist` in `config.yaml`. The approval card says "command" and times out after 300 s by default
  (`approvals.timeout`). Plain `/approve` resolves the oldest pending prompt; `/approve all` approves everything.
- Typed `/planmode approve` approves the submitted revision, else the newest plan (v0.2 behavior), then asks Hermes
  to start the work via `ctx.inject_message`. Typed while its prompt is still open, approving the prompt continues
  that turn; a deny or timeout does not cancel the typed approval, and the work starts in the next turn. It never
  starts twice. This works on the CLI, and on the gateway and TUI/Desktop
  (Hermes main) only with `plugins.entries.plan-mode.allow_gateway_injection: true`; otherwise the next message
  starts it.
- Core `/plan` now turns on enforced plan mode for the session before the agent's first tool call
  (`plan_mode.enforce_builtin_plan`, default true). The marker is matched anywhere in the message, so group and
  reply prefixes work. A refused activation adds nothing and blocks nothing.
- New planning turn note: read-only exploration, `clarify` for genuine ambiguity, an absolute plan path, show the full
  plan, then submit; no "should I proceed?" in prose. The note and the `/planmode on` reply give the current
  timestamp for the plan file name, since the model cannot read the clock while `terminal` is blocked. While
  executing, a one-line pointer to the approved plan is added each turn until the todo list is all completed or
  cancelled (todo writes only; a read of an earlier list does not count), `/planmode done|off`, a new activation,
  `/new` or `/reset` (also when the gateway runs the reset hook outside the session), or 100 turns. On TUI/Desktop
  the tab's command and hook copies stay in step, except after a session-key rotation or a reopen in a new tab.
- A ≤200-char system-prompt hint names `plan_mode` for multi-step or risky changes (`plan_mode.agent_hint`, default
  true; skipped where the host has no prompt-section API).
- Add `/planmode show [file]` (read-only, ≤3500 chars, gated to the session's own profile) and `/planmode done`. `/planmode status` reports the phase
  and the last submission.
- A one-line footer on chat platforms: "⏸ Plan mode: nothing changes until you approve the plan." while planning,
  "Plan progress n/m · now: <step>" while executing (`plan_mode.footer: auto|off`). Never on CLI, TUI, Desktop (also
  not for a chat session resumed there), API server, webhook, cron or delegate subagents, and never on replies over
  1800 chars. Delegate subagents get no turn note either.
- Allow Tool Search's `tool_search` and `tool_describe` in plan mode (a `tool_call` is checked as the inner tool), so
  the model can find `plan_mode` and `todo_list` without a detour.
- `args_hint` is now `[on|status|show|approve|reject|done|off] [task]`, which Telegram's command menu accepts.
- Register `pre_approval_request`, `post_approval_response` and `transform_llm_output`; `plugin.yaml` 0.3.0.
- CI tests Hermes `v2026.9.14`, `v2026.9.21`, `v2026.9.24` and a pinned `main` (Python 3.14), checks that the real
  Hermes modules import before the tests run, and runs `hermes plugins validate` and `doctor` (`compat` where the
  host still has it).
- Not in this release: an autonomy choice at approval, clear-context-and-implement, and a live mid-turn plan or
  progress card (needs a new upstream plugin API, proposed in
  [NousResearch/hermes-agent#133306](https://github.com/NousResearch/hermes-agent/issues/133306)).

## 0.2.0 - 2026-09-26

- Add an identity-bound native tool `plan_mode` (toolset `plan-mode`, actions
  `on`, `status` and `off`). It binds to the same session identity as
  `/planmode` and refuses without one; enforcement is unchanged. On hosts with
  Tool Search on (the Hermes default from v2026.9.24) the tool sits behind
  `tool_search`: a live check showed a model reaching it only when told to use
  it. `/planmode on` stays the reliable way in; live Telegram use is not
  covered by the automated tests.
- Record provenance (`entered_by`: `user` or `agent`). The tool's `off` ends
  only a plan mode the agent entered in the same activation; approval and
  rejection stay slash-command-only. A user `/planmode reject` makes an
  agent-entered activation user-owned. `plan_mode` is allowed while plan mode is
  on, and an agent-entered plan mode adds one sentence to the turn note.
- A UI turn's activation links the tab's session-key alias at once, so the
  tab's `/planmode status|approve|off` reach it before the next hook.
- Document the Telegram 60-entry command-menu cap and the
  `platforms.telegram.extra.command_menu.priority: [planmode]` pin.

## 0.1.7 - 2026-09-26

- Track a plan write as approvable only after `post_tool_call` reports status
  `ok` for the same `tool_call_id`; failed, blocked or cancelled writes add
  nothing. Hosts that pass no `tool_call_id` keep the 0.1.6 tracking.
- Refuse `/planmode approve` when the session has no tracked plan file.
- With an unlinked rotated TUI/Desktop command key, `status` reports
  `unresolved` and `on` is refused instead of creating a second state.
- Clear a rotation alias's stored state when it drops out of the 256-alias cap.
- Gateway, TUI and Desktop enforcement is released in Hermes `v2026.9.24`
  (0.21.5); CI adds it. Document the gateway `/planmode on` → `/new` limit.

## 0.1.6 - 2026-09-23

- Allow `skill_view` in plan mode when Hermes' own
  `agent.skill_preprocessing.load_skills_config()` reports `inline_shell`
  off at call time; keep it blocked when inline shell is on or the loader is
  unavailable, raises, or returns a non-dict.
- Document the supported surfaces honestly: the classic CLI on released Hermes
  (≤ 0.21.4); gateway/TUI/Desktop only on builds containing
  NousResearch/hermes-agent `5943347a2a` and `35fdb4608a` (after
  `v2026.9.21`). Add disclosures, list every internal Hermes seam (seven) with its
  missing-seam behavior, and fix source citations and the
  `extra_allowed_tools` config path.
- Gate real-Hermes tests on the session-binding features instead of the version
  string, and run CI against Hermes `v2026.9.14`, `v2026.9.21` and the pinned
  `main` commit.
- Refuse `off`, `approve` and `reject` when the bound session profile differs
  from the plugin registration profile, or the registration profile is
  unknown, exactly as `on` already did; nothing is written. `status` stays
  read-only.

- Review follow-ups: a raising `get_active_profile_name` now leaves the
  registration profile unknown (bound sessions refuse state-changing
  commands) instead of assuming `default`; README lists the seventh internal
  seam (`ctx._manager.home_path`) and the cron exemption for key-less calls;
  the real-PluginManager CLI test now asserts `terminal` and out-of-plans
  `write_file` are blocked.

## 0.1.5 - 2026-09-23

- Refuse activation when a bound session profile cannot be matched to the
  plugin registration profile, and recognize Hermes custom homes by the same
  public profile-name helper Hermes uses.
- Exempt ContextVar-marked cron runs from only the key-less process-wide guard,
  while keeping bound plan-mode sessions enforced.

## 0.1.4 - 2026-09-23

- Preserve adopted TUI/Desktop plan mode across gateway process restarts by
  keeping CLI-only PID liveness metadata out of the durable UI state.

## 0.1.3 - 2026-09-23

- Preserve TUI/Desktop plan mode when an agent rebuild emits a reset callback
  without an explicit old session id.
- Refuse cross-profile TUI activation through a launch-profile plugin instance,
  inherited slash-worker activation, and the first unbound Hermes 0.21.3
  messaging-gateway command without treating an inherited gateway environment
  flag as process admission.
- Limit unbound fail-closed enforcement to active state owned by the current
  process so another process's abandoned state does not block cron.
- Let an unlinked tab query `status`, clarify unavailable/refused command
  responses, and document the non-default pre-turn rotating-compression gap.

## 0.1.2 - 2026-09-23

- Distinguish a CLI that merely imports gateway modules from an actually engaged
  server session, and ignore inherited parent TUI/Desktop identity in child CLIs.
- Keep TUI command state reachable after stable-UI adoption and session-key
  rotation, preserve links when re-enabling, and refuse an unlinked rotated
  command instead of guessing across UI tabs.
- Refuse the first unbound legacy TUI/dashboard command using Hermes' gateway
  process admission marker, without confusing a CLI that imports gateway code.
- Limit finalization cleanup to the current process's CLI state and avoid
  destructive Windows PID probes.
- Block `skill_view` unconditionally in plan mode; profile config cannot safely
  prove that inline shell is disabled.
- Revalidate the fixed plan root before each writer to block post-activation
  symlink swaps.
- Add a real upstream TUI `slash.exec` profile-isolation regression, documenting
  the confirmed upstream cross-profile routing limitation.

## 0.1.1 - 2026-09-23

- Refuse activation on legacy server command paths that do not bind a session,
  and keep legacy CLI state fail-closed if a later turn derives another key.
- Preserve TUI/Desktop plan mode across session-key rotation with the stable UI
  tab id, without letting unrelated resets clear active sessions.
- Refuse symlinked plan roots, bind plans to the session workspace, and track
  plan files per session for safe explicit or newest-plan approval.
- Block `skill_view` when inline shell is enabled, isolate inherited nested CLI
  state, and expire CLI state on finalization or dead PID detection.

## 0.1.0 - 2026-09-23

- Add enforced per-session plan mode for Hermes CLI, gateway, and TUI.
- Allow absolute writes only inside the fixed `.hermes/plans` directory.
- Add approval/rejection one-shot notes, persistent session state, tests, and
  manual acceptance instructions.
