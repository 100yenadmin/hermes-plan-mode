# Manual acceptance test

Use a disposable Hermes home. Do not point these steps at a real profile.

```bash
export HERMES_HOME="$(mktemp -d)"
mkdir -p "$HERMES_HOME/plugins"
cp -R /path/to/hermes-plan-mode "$HERMES_HOME/plugins/plan-mode"
hermes plugins enable plan-mode
```

Run the following on each supported surface (CLI, gateway, and TUI):

1. Start a session in a disposable workspace and run `/planmode on write a
   hello-world plan`.
2. Confirm the reply prints an absolute
   `<workspace>/.hermes/plans` directory and explains the blocked surfaces.
3. Ask Hermes to inspect a file and search the web. Confirm read-only calls are
   allowed.
4. Ask Hermes to run `pwd`, execute code, call an MCP tool, send a message, and
   write outside the printed directory. Confirm each returns a `Plan mode is
   on` block message.
5. Ask Hermes to write a plan using a **relative** path. Confirm it is blocked
   and tells the model to use an absolute path.
6. Ask Hermes to write
   `<absolute-plans-dir>/YYYY-MM-DD_HHMMSS-hello-world.md`. Confirm it succeeds.
7. Run `/planmode status`; confirm the entry time, directory, and plan path.
8. Run `/planmode reject add rollback details`, then send a normal turn.
   Confirm the rejection note appears once and plan mode remains active.
9. Run `/planmode approve`, then send a normal turn. Confirm the approval note
   appears once and implementation tools are unblocked.
10. Re-enter plan mode and run `/new` or `/reset`. Confirm plan mode is cleared
    for the replacement session.

CLI compression check: enter plan mode, trigger or wait for compression, then
repeat a blocked terminal call. It must remain blocked because CLI state is
keyed by process, not the rotating `session_id`.

Do not use Codex app-server for this test. Its native `exec` and `applyPatch`
bypass Hermes `pre_tool_call`, as documented in the README.
