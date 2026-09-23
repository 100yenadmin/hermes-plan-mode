# Changelog

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
