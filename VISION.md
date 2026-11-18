# Vision

## Goal

Plan Mode makes a Hermes agent plan before it changes anything, the way the Claude Code and Codex plan modes do. The agent
explores read-only, asks what it needs to know, writes a plan, and builds only after a person approves it. We measure it against
both of those plan modes and ship only what tests and live runs show working.

## Who it is for

- People who use Hermes for coding or multi-step work and want to see and approve the plan before any file changes.
- People who reach their agent through a chat app and want the same approval step there, with buttons where the app has them.
- The agent itself: it can enter plan mode for risky work. Plan mode a person starts holds until that person approves.

## What good looks like

1. Nothing changes before approval.
2. Plans are complete enough to approve and small enough to read.
3. Planning costs about as many model calls and tokens as the other plan modes.
4. The agent asks a few clear questions with named options, not many vague ones.
5. The build follows the approved plan, and long builds get finished.
6. It works wherever the person is, and the person decides what happens after approval.

## Coverage on every Hermes surface

| Surface | What plan mode does there |
|---|---|
| Classic CLI | Enforced (Hermes 0.21.3 and newer). Approval card in the terminal; Hermes' own todo list shows progress |
| TUI and Desktop, including `hermes serve` sessions | Enforced (Hermes 0.21.5 and newer). Approval card; live progress card |
| Telegram, Slack, Discord | Enforced (Hermes 0.21.5 and newer). Approval buttons; live progress card |
| Other messaging platforms | Enforced. Buttons, a numbered list or `/approve`; live progress card where messages can be edited |
| API server, webhooks, cron, ACP | Approval goes through Hermes' own approval prompt instead of the card |
| Kanban workers and `delegate_task` helpers | No plan mode of their own: a Kanban worker without a session key (all of them on Hermes 0.23.1) is told its card is its plan, and a helper cannot turn plan mode on or off or submit a plan |

The README's "What you see on each channel" and "Compatibility" sections have the details and the Hermes versions.

## Non-goals

- **Not a sandbox.** Plan mode blocks tools that Hermes dispatches through its `pre_tool_call` hook. Hermes' Codex app-server runtime
  and the TUI's `/background` and `btw` side agents are outside it (see the README's Disclosure).
- **No approval screens of its own.** Approval uses Hermes' own approval and question prompts on each surface, so there is no
  per-platform code.
- **No changes to Hermes itself.** The plugin uses Hermes' plugin hooks only. It makes no network calls, starts no subprocesses
  and has no self-updater.
- **Not a task runner.** Turning an approved plan into a long-running goal would build on Hermes' own `/goal`, not replace it (#32).

## Roadmap

The roadmap is the pinned issue [#33](https://github.com/100yenadmin/hermes-plan-mode/issues/33): what ships next, and the parked
items with the trigger that reopens each one.
