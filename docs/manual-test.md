# Manual acceptance test

Use a disposable Hermes home. Do not point these steps at a real profile.

```bash
export HERMES_HOME="$(mktemp -d)"
mkdir -p "$HERMES_HOME/plugins"
cp -R /path/to/hermes-plan-mode "$HERMES_HOME/plugins/plan-mode"
hermes plugins enable plan-mode
```

Run the following on each supported surface (CLI, gateway, TUI, and Desktop):

Enforced in the classic CLI on released Hermes. Gateway/TUI/Desktop need Hermes
`v2026.9.24` (0.21.5) or later; earlier builds refuse `/planmode on` there
instead of pretending. On such an earlier build, step 1
on gateway, TUI or Desktop must return the clear session-binding refusal;
continue those flows only on a build that includes the matching commit.

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
    for the replacement session (gateway: run one turn first; see the README).
11. In TUI/Desktop, enter plan mode, trigger `/compress`, then repeat a blocked
    terminal call. It must remain blocked after the durable session key rotates.
12. Create a second chat in the same workspace and write a lexically later plan
    there. Back in the first chat, run `/planmode approve`; confirm it approves
    only the first chat's newest plan. Repeat with `/planmode approve <file>`.
13. Enter plan mode and call `skill_view` with the default profile config
    (`skills.inline_shell` unset or `false`); confirm it is allowed. Set
    `skills.inline_shell: true` in the disposable profile's `config.yaml`, call
    `skill_view` again, and confirm it is blocked with a message naming
    `skills.inline_shell`.

Agent tool (`plan_mode`), on the CLI, the TUI and a gateway chat (Telegram):

14. With plan mode off, ask "write a plan for a hello-world script before doing
    it". Confirm the agent calls `plan_mode` with `action="on"`, then a
    `write_file` outside the plans directory and a `terminal` call are blocked,
    and a plan write under the plans directory succeeds.
15. Ask the agent to finish; confirm `plan_mode(action="off")` succeeds (the
    plan file stays) or that it tells you to run `/planmode approve`.
16. Run `/planmode on` yourself, then ask the agent to turn plan mode off.
    Confirm the tool refuses with `Plan mode was entered by the user; only
    /planmode approve, reject or off can end it.` and enforcement stays on.
17. Ask the agent to approve its own plan. Confirm it cannot: the tool offers
    only `on`, `status` and `off`.
18. In TUI/Desktop, after the agent enters plan mode, run `/planmode status`
    in the same tab; it must report `Plan mode: on`.
19. On Telegram, confirm typing `/planmode` works even if the bot menu hides it;
    with `platforms.telegram.extra.command_menu.priority: [planmode]` it shows.

CLI compression check: enter plan mode, trigger or wait for compression, then
repeat a blocked terminal call. It must remain blocked because CLI state is
keyed by process, not the rotating `session_id`.

Do not use Codex app-server for this test. Its native `exec` and `applyPatch`
bypass Hermes `pre_tool_call`, as documented in the README.
