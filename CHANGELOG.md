# Changelog

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
