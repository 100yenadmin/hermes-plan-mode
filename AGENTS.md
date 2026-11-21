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

## Release and escalation
- Lane by changed path, not by plugin (below, "the orchestrating owner" and "the human owner" are this project's owners). The careful lane covers changes to storage or migrations, deletion or compaction, locking, provider routing, streaming or retries, permissions, shared hooks, dependencies or update machinery, a host-wide install (a shared plugin root or processes, including one that can't be isolated), and anything with uncertain recovery. On top of the fast-lane proof floor below, it adds mechanism tests (old-state upgrade, restart or interruption, concurrency, recovery after new writes), a consistent backup, a recovery rehearsed on a copy that keeps post-upgrade data, and a canary on one already-authorized live install, running real operations with stated pass/fail thresholds. For an install that can't be isolated, the orchestrating owner decides the blast radius. Everything else takes the fast lane.
- Fast-lane proof floor for the smallest proven fix: an independent non-author review and green CI at the exact final SHA, targeted regression tests, and a smoke test of install, startup and the affected workflow against the current release. Reaching that proof should take hours, not days. Don't wait for GA, a release date, unrelated work or a benchmark campaign. Once it passes, release on the cadence below. A remaining P0 doesn't block the other fixes if it is contained (verified workaround, no new exposure, known-issue note, named fix owner, next-update date); otherwise the orchestrating owner decides the trade-off. In either lane, before shipping, name a watcher whose wake route has been seen to fire and a ready rollback or disable command, then watch for 72 h or one update cycle. On corruption, disclosure, an unauthorized action, a critical-path failure or a breached threshold: stop, contain, roll back or disable, tell the orchestrating owner and keep the evidence. A published release can't be recalled from auto-updating users; when that matters, ship a fast corrective release with clear user instructions.
- Tests follow the diff: if the plugin has slow or benchmark suites, keep a short map from each one to the code and behavior it measures. A release runs only the suites whose inputs changed and records each skip in the release note ("suite X skipped: nothing it measures changed"). The full suite runs on its own nightly or weekly schedule, off the release path. A regression it finds gets its own fix and never gates an unrelated hotfix.
- Severity and cadence (binding release rules for maintainers; not a support promise to users). P0 is data loss, disclosure or an unauthorized action (even for one user), or the default critical workflow broken with no safe workaround: contain a suspected P0 first and tell the orchestrating owner at once. P1 is a supported workflow blocked or materially degraded for some users, without P0 harm. An owner's priority call goes into your plan and changes the order of work, never the severity. P0 fixes ship immediately; ready P1 fixes go out in at most one bundled release a day; features ride a regular train (weekly by default, set per plugin by the orchestrating owner). Never hold a ready fix past the next daily bundle, and never cut a release that carries nothing a user needs.
- Anti-stall clocks, which never reset. A P0 with no safe path within 2 h of triage goes to the orchestrating owner. For a fix candidate with no decisive test, review or authorization result within 4 h, send the orchestrating owner the blocker, the remaining evidence, the fix owner and the next result time; a longer test needs the orchestrating owner's explicit OK, once, and a missed checkpoint re-escalates. A ready fix held 4 h needs a named blocker; send it to the orchestrating owner with a deadline for their decision. Running out of time never waives proof; it forces a decision.
- Who decides: maintainers make reversible technical choices within the permissions they already have, then report. The orchestrating owner decides cross-plugin priorities, user-impact trade-offs, unresolved high risks, blast radius and longer tests; an escalation carries the evidence, the options, a recommendation, a deadline and a safe fallback. The human owner decides new access or credentials, money, anything public in their name and destructive actions. This standard grants no new authority to publish, install or spend: live installs happen only in the guarded install window (backup first, then one restart), run by whoever the orchestrating owner has assigned to it, never on your own. Silence allows only a default that is already delegated and passes the gates above; it never grants new authority or accepts new risk: on silence, hold or contain, and escalate again.

Maintainer agents: the full internal standard is in eng-standards policies/release-and-escalation.md.

## Release closeout (maintainers, after every release)

After a release is published:

1. Close every issue the release fully served, with a comment naming the version. Never close an umbrella or parent issue
   whose own acceptance is not met.
2. Tick the boxes it completed on the roadmap issue (#33), other tracking issues, and closed issues whose checklists
   shipped. Close a milestone once all its issues are closed.
3. Update every README line and the #33 status that names the latest release or what ships next.
4. Post one closeout line as a comment on the release pull request: issues closed, boxes ticked, docs updated (or
   "none").

## Filing issues

- Search first: save the key words to a file, then run `gh issue list -R 100yenadmin/hermes-plan-mode --state all --search "$(cat '<search file>')"`. Never type model-written text into a command.
- Use the bug form headings: What happened, What you expected, Steps to reproduce, Plugin version, Hermes version, Where it happened, Operating system, Diagnostics, Relevant log lines.
- Maintainers filing from the CLI always pass labels: `--label bug,triage-pending` (or `triaged` once triaged), plus `agent-drafted` when an agent wrote it.

## Security

Report vulnerabilities privately: https://github.com/100yenadmin/hermes-plan-mode/security/advisories/new
