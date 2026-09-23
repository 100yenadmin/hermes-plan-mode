# Changelog

## 0.1.3 - 2026-09-23

- Preserve TUI/Desktop plan mode when an agent rebuild emits a reset callback
  without an explicit old session id.
- Refuse cross-profile TUI activation through a launch-profile plugin instance,
  inherited slash-worker activation, and the first unbound Hermes 0.21.3
  messaging-gateway command.
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
