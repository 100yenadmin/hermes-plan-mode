# AGENTS.md

Guidance for AI coding agents, and for people, working in this repository.

## What this is

Plan Mode is a standalone Hermes Agent plugin that enforces read-only planning until the user approves a plan. It registers the `/planmode` command, the `plan_mode` tool and approval/session hooks across CLI, TUI, Desktop, messaging and API surfaces. Enforcement covers tools dispatched through Hermes' `pre_tool_call` hook; the README documents host-version requirements and paths outside that hook.

## Layout

- `plugin.yaml`: plugin identity, Hermes requirements, tools, hooks and settings.
- `__init__.py`: plugin entry point.
- `plan_mode/plugin.py`: session state, commands, tool enforcement and hooks.
- `plan_mode/approval.py`: approval ledger, plan review and approval text.
- `plan_mode/render.py`: planning instructions and display text.
- `skills/plan-mode-report-problem/SKILL.md`: the report-a-problem skill, registered with `ctx.register_skill`. Its
  `###` headings must match the labels in `.github/ISSUE_TEMPLATE/bug_report.yml` (a test checks this).
- `tests/` and `pytest.ini`: isolated fixtures and pytest configuration.
- `.github/workflows/ci.yml`: unit tests and real-Hermes compatibility checks.
- `README.md` and `docs/manual-test.md`: behavior, limitations and manual checks.

## Setup and checks

CI is `.github/workflows/ci.yml`. There is no build step and no committed bundle.

```bash
python -m pip install pytest pyyaml
python -m pytest -q
```

CI also installs Hermes (pinned and main) and runs `hermes plugins validate .` and `hermes plugins doctor . --ci` against it. To run
those locally, install Hermes in a throwaway virtualenv, set a temporary `HERMES_HOME`, and export `HERMES_DISABLE_LAZY_INSTALLS=1`. Never
point them at a real Hermes home.

## Rules

- Keep each change small and focused: one change per PR.
- Don't bump versions, create tags, or add CHANGELOG release headings. Maintainers cut releases.
- Preserve the approval boundary: no mutating tool dispatch before approval, except permitted plan-file writes.
- Keep plan targets inside the fixed plans directory; preserve session/profile isolation and reviewed revision snapshots.
- Keep the documented host compatibility fallbacks and enforcement limitations explicit.
- Use fake contexts or disposable workspaces for reproduction; no billed API calls or real messaging in automated tests.
- Never commit secrets, tokens, real user data, or machine-specific paths.
- Tests use a temporary `HERMES_HOME`, never a real one.

## Filing issues

- Search first: save the key words to a file, then run `gh issue list -R 100yenadmin/hermes-plan-mode --state all --search "$(cat '<search file>')"`. Never type model-written text into a command.
- Use the bug form headings: What happened, What you expected, Steps to reproduce, Plugin version, Hermes version, Where it happened, Operating system, Diagnostics, Relevant log lines.
- Maintainers filing from the CLI always pass labels: `--label bug,triage-pending` (or `triaged` once triaged), plus `agent-drafted` when an agent wrote it.

## Security

Report vulnerabilities privately: https://github.com/100yenadmin/hermes-plan-mode/security/advisories/new
