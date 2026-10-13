# Changelog

## Unreleased

- New opt-in lever `exec_skill_guard`: while an approved plan is executing, block loading the TDD, planning and
  code-review process skills. A user message mentioning TDD, test-first, red-green or code review lifts the guard for
  TDD and code-review skills; planning and brainstorming skills stay blocked.

## 0.4.6 - 2026-10-13

Docs only; no code changes. Behaviour is identical to 0.4.5.

- README corrections:
  - PARITY-3's in-run environment check observed only Codex's command output, so for plan-mode only the before and
    after checks apply.
  - One of plan-mode's 9 hidden-preference hits came from a question that named no options.
  - The agent hint applies when Hermes next loads plugins.
  - The profile must grant the `plan-mode` toolset.
  - `todo_list` mirroring under `lean` is for plans of 4+ steps.
- README: "Measured against Codex plan mode" leads with PARITY-3 (0.4.5, the same test environment for both
  products): every threshold met, 1.61× end to end (95% CI 1.33–1.79). Follow-up on the rename task: #28.

## 0.4.5 - 2026-10-13

- `rename_scope` and `test_plan_only` join the default lean set. In the pre-registered 0.4.5 stress (S45, 26 runs per
  arm), rename plans that narrowed the leftover search fell 4 → 0 of 4 and unrequested assertions 3 → 0; test plans with
  git status/diff checks fell 10 → 0 of 26. Every guard held: no changes before approval, 26/26 verified in both arms,
  the same hidden-preference hits, and T8 passing tests 24.9 vs 28.4 per run, above the 85% bar.
- `choice_record`, `edit_batch` and `brief_final` stay opt-in. `choice_record`: 6 of 6 plans attributed the clarify
  choice with it, but read directly 4 of 6 already did without it, so the deficit was not shown. A labelled arm ran
  `edit_batch` and `brief_final` alone: edit rounds 49 → 27, final reply 128.5 → 64 tokens, and T8 tests rose
  28.4 → 32.3 (mean per run, labelled). The earlier drop was observed with all five enabled (#26).
- Labelled, not gated: the S45 arm with the three planning levers (the two above plus `choice_record`) took 1.11× the
  baseline's wall time (95% CI 0.97–1.25).
- The execute hint is unchanged from 0.4.3 and 0.4.4.

## 0.4.4 - 2026-10-13

- Five new opt-in wording levers. Four met their bars in the pre-registered 0.4.4 stress (S44), but together the five cut
  passing tests on the refactor task below the guard, and a fresh replication confirmed it, so none joined lean (#26):
  - `rename_scope`: rename plans that narrowed the leftover search 4 → 0 of 4, unrequested assertions 2 → 0;
  - `test_plan_only`: plans with git status/diff checks 7 → 0 of 22;
  - `choice_record`: scored 2 → 0 of 4 and failed. Its probe missed the "User decision:" form the lever produces; read
    directly, it was 2 → 4 of 4. To be re-measured;
  - `edit_batch`: execution rounds with edits 48 → 20 (T7+T8);
  - `brief_final`: median final reply 117.5 → 62.5 output tokens;
  - guard: T8 passing tests per run 33.5 → 26.25 (S44, n=4) and 29.1 → 22.3 (replication, n=8).
- With the five off (the default), every model-facing text is byte-identical to 0.4.3.
- **Fix:** on hosts that approve by `clarify` (yolo, unattended), the same-turn approval note now also carries
  `edit_batch` and `brief_final` when they are the only levers on.
- **Correction:** 0.4.3's notes said refactor_scope missed its bar. That was a probe error; re-scored, rename plans
  narrowed 4→0 of 4 and added unrequested asserts 3→0 (#24).
- **Correction:** the README's PARITY-2 caveat said the effect of the different test environments was not measured.
  A re-analysis estimates Codex's execution time without its pytest setup cost, putting the execution ratio at about
  1.39–1.42× rather than 1.26×. The README now says so.

## 0.4.3 - 2026-10-12

- Three new lean levers, each aimed at a cause PARITY-2 measured against Codex plan mode:
  - `test_commands`: test commands the way the repo runs them, falling back to `python3 -m pytest`;
  - `todo_inline`: no rounds spent only on `todo_list`;
  - `choice_fidelity`: a recommended clarify choice keeps every stated requirement.
- `refactor_scope` is opt-in only. It missed its bar: leftover searches were still narrowed.
- `bridge_calls` also gives the agent hint and the agent-entered result the `tool_call` form, so entering or leaving
  plan mode needs no tool search (#22).
- **Typed `/planmode approve` (lean):** it now carries the same todo guidance as approval through the prompt, the
  four-step qualifier plus the `tool_call` form, rather than the bare form.
- **`extra_allowed_tools`:** when it allows terminal, code execution, delegation or MCP tools, the lean planning note
  no longer calls them blocked.
- Classic text, light reminders and tool decisions are unchanged. With the new levers off, the planning notes,
  executing reminders and light notes are byte-identical to 0.4.2. The agent hint and the agent-entered note change
  under `bridge_calls` (#22).
- **`plan_mode on` result:** with `extra_allowed_tools` set, it names the tools the configuration allows instead of
  calling them blocked.
- **Cleanup:** a custom lever set with `todo_inline` but not `todo_batch` also clears the executing reminder when no
  todo list was made.

Measured (TEST-PLAN-S43, pre-registered; 0.4.2 lean levers vs 0.4.3, same build, run at the same time, 18 runs each):
- **Bare `python` in plans:** 18 → 0. Runs that spent a round retrying a test command: 17 → 0.
- **Rounds spent only on the todo list:** 27 → 7.
- **CSV task, plans dropping the required round trip for NaN and Infinity:** 2 of 6 → 0.
- **Execution time:** 0.88× (95% CI 0.75–0.97). End-to-end time unchanged at 0.99× (0.90–1.05). Median tokens 358k →
  304k.
- **Guards held:**
  - no changes before approval and no commits;
  - tests passed in 18 of 18 runs for both;
  - hidden preferences found 4 of 4 for both;
  - questions per run fell from 1.44 to 1.06.

## 0.4.2 - 2026-10-12

- Three new lean levers remove unnecessary planning rounds.
- `bridge_calls` gives direct tool_call examples for plan_mode and todo_list, removing lookups.
- `blocked_tools` names the blocked tools and tells the agent to put commands and tests in the plan instead of retrying.
- `skill_policy` skips process skills while planning and follows the approved plan during execution.
- Light reminders keep whole sentences within 400 characters. Classic text and tool decisions are unchanged.

Measured (PARITY-2, pre-registered and blind-adjudicated; plan-mode 0.4.2 vs Codex plan mode on gpt-6.1-sol medium,
76 runs):
- The rounds that bought nothing fell, 0.4.1 → 0.4.2 (out of 38 runs):
  - `plan_mode` lookups 38 → 0;
  - blocked terminal or code attempts while planning 36 → 0;
  - process-skill loads while planning 30 → 1;
  - `todo_list` describes 8 → 0.
- **Plan-writing skills:** with them installed, loads fell from 7 of 7 runs to 2 of 7. That missed the ≤1 target and
  passed only on the fallback (at most half the 0.4.1 baseline).
- **Every threshold against Codex held:**
  - 0 changes or commits before approval;
  - tests 35 vs 35 of 35;
  - hidden preferences 10 vs 10 of 10;
  - judge preference 0.91 (Claude) and 0.88 (Codex).
- **Speed:** 1.28× Codex's end-to-end time (95% CI 1.16–1.40), against 1.45× for 0.4.1.

## 0.4.1 - 2026-10-11

- **Plan once, on by default on TUI, Desktop and `hermes serve`** (lean profile). The agent writes the plan once, in
  its reply between a `<proposed_plan>` line and a `</proposed_plan>` line, and the plugin saves that text as the plan
  file, instead of writing the file and then repeating the plan in the reply. The CLI and the chat platforms are
  unchanged. Where the reply would not be shown (interim messages off, or commentary hidden for Codex models), the
  agent writes the plan file as before.
  - In its pre-registered round (round 8, blind adjudication, gpt-6.1-sol medium, Hermes v0.21.6, eight tasks, three
    runs each) it finished 22% faster (wall time 0.78×, 95% CI 0.73–0.85; faster on 8 of 8 tasks) and used about 23%
    fewer cost-weighted tokens (0.77×, CI upper bound 0.94). Test results were the same (24 of 24), with no approval
    leaks and no commits. Both blind judges scored the plans the same (plan fit + clarity 9.58 vs 9.50 and 9.96 vs
    9.92 of 10). The plugin saved the plan from the reply in 24 of 24 runs; in one, the agent later added a
    correction note to the saved file while implementing.
  - Round 7 measured it on the 0.4.0 lever set with the same speed and quality result, apart from one commit after a
    TDD skill that the control column also had (#18).
  - Checked by eye in one TUI and one Desktop session: the reply shows the plan between the two tag lines, then
    Hermes' approval card; approving starts the implementation in the same turn.
  - To turn it off, set `levers` to the lean list without `plan_once` (see the README).
- **plan_once v2.** It saves only the `<proposed_plan>` block from the reply, refuses zero, several or unbalanced blocks
  with a reason the agent can act on, and after approval says the saving is done, also after a typed
  `/planmode approve`.
- **plan_once checks visibility per session** (#16). The reply counts as visible only when both the display settings
  at the session's first plan_once use and the current settings say so, so turning `show_commentary` on after that
  first use no longer makes plan_once save text that Hermes still sends to the reasoning channel. A change made
  before the first plan-mode turn, or in a process that has served more than 256 sessions since, is not caught:
  Hermes reads the setting when it builds the agent, so start a new session after changing it (as the README says).
- **Two measured levers, opt-in only:** `commit_override` adds "This overrides any commit step in a loaded skill" to
  the no-commit rule (#18), and `approval_ask` tells the agent to ask the approval question even when the user said
  not to ask questions (#19). Round 8 measured them together against the 0.4.0 default:
  - wall time 0.95× (CI 0.87–1.03), but cost-weighted tokens 1.06× (CI 0.88–1.21), which missed the pre-declared
    non-inferiority bar of 1.10;
  - with "Use test-driven development." added to the task, both lever sets made 0 commits in 12 runs (the TDD skill
    loaded in 12 of 12), so the override's effect could not be measured;
  - with approvals off and "do not ask questions" in the task, the approval question came unprompted in the first
    turn in 7 of 8 runs without `approval_ask` and 6 of 8 with it; in the other runs it came after one nudge;
  - in the eight-task comparison the 0.4.0 default (the control) committed in 1 of 24 runs, after a TDD skill loaded
    on its own.

  Neither joins a profile; #18 and #19 stay open.
- **README.** The no-commit rule is described as an instruction; the comparison table says Codex continues in a new
  turn after one click; a note says which channels we have exercised end to end and which are supported through
  Hermes' own approval and question UIs.
- **Classic is unchanged:** `plan_profile: classic` still gives the 0.3.5 texts byte for byte.

## 0.4.0 - 2026-10-11

- New setting **Plan profile** (`plan_profile`), `lean` by default. Lean makes compact plans faster and leaner. It
  applies with Plan style `compact` and no Plan skill; `plan_style: core` and a Plan skill keep the 0.3.5 behaviour.
  Set `plan_profile: classic` to get the 0.3.5 behaviour back everywhere. Lean changes the following:
  - **The planning note.** It supersedes core `/plan`'s craft: no 2–5-minute task slicing, no copy-paste code, no
    red/green ceremony unless you ask for TDD, and the plan file goes only at the absolute path given.
  - **The plan format.** It is Codex-style: Summary; Key changes grouped by behaviour, still naming every touched file
    and every consumer of a changed interface; Test plan with exact commands; Assumptions. It is concise by default.
  - **Questions.** The agent still asks with `clarify` before it writes the plan about choices only you can make,
    and it asks only what reading the repo cannot settle. It prefers 1 question and asks at most 3, each with 2–3
    choices written as label — consequence, recommended first.
  - **Fewer bookkeeping calls.**
    - `todo_list` is used only for plans with 4 or more steps, and status updates ride along with real work.
    - The full planning note is sent on the first planning turn and after a denial; later planning turns get a short
      reminder.
    - The executing pointer clears when the approving turn ends without a todo list.
  - **Smaller behaviours.**
    - The approval result carries the approved plan text (bounded).
    - Re-entering plan mode points at the previous plan.
    - "Just do it" while planning means: plan it and submit.
- **Why lean is the default.** Benchmark rounds with bars written before the runs, blind adjudication, the same model
  (gpt-6.1-sol, medium) and the same Hermes (v0.21.6), on eight coding tasks:
  - Lean finished 42% faster than 0.3.5 (wall time 0.58×, 95% CI 0.55–0.62; faster on 8 of 8 tasks).
  - It used 28% fewer cost-weighted tokens (0.72×, CI 0.63–0.80).
  - Test results were the same, and there were no approval leaks.
  - On two tasks where the user had a preference the prompt did not state, it asked and matched it in 10 of 10 runs
    (0.3.5: 10 of 10). On the eight tasks without one, it asked 15 questions (0.3.5: 22). These figures come from
    round 5, two runs per task.
  - Two blind judges from different model families scored plan and code quality the same as or better than 0.3.5
    (round 3, three runs per task).
  - An earlier wording of the question limits made the agent stop asking (0 of 5 hidden preferences found). It was
    caught by its own bar and replaced before release.
  - The no-commit rule is still an instruction. Across all lean benchmark runs the agent committed in 2 of 108,
    both after loading a TDD skill (0.3.5: 0 of 70); tracked in #18.
- **Experimental, not part of any profile:** `plan_budget` gives compact plans a length budget: what changes and who consumes
  it, not how to write it. In a pre-registered round it halved plan length and finished 24% faster, with the same test
  results. One of the two blind judges scored plan fit and clarity lower, so it stays opt-in (the `levers` setting;
  see the README).
- **Experimental, not part of any profile:** `plan_once` writes the plan only once on TUI, Desktop and `hermes serve`. The
  agent writes the plan as its reply, and the plugin saves exactly that visible text as the plan file. It was 19%
  faster in a pre-registered round, but it did not hold the plan-quality bar, so it stays opt-in until a revised
  version passes. It needs Hermes 0.21.5 (v2026.9.24) or later; on older Hermes it stays off and the plan file is
  written as usual.
- `plugin.yaml` declares every hook the plugin registers, including `post_llm_call`, `transform_tool_result` and
  `post_api_request`, which do nothing unless their lever is on.
- The classic CLI approval prompt no longer claims the plan is shown above. It now says the plan is in the reply
  above, and if it is not, to deny and run `/planmode show`. With `display.streaming: false` the reply may not be
  printed before the modal prompt.
- The note shown when Hermes approves a submit automatically now names every cause. Besides yolo and
  `approvals.mode: off`, that includes unattended surfaces such as the API server or a webhook, which auto-resolve
  approvals on recent Hermes. Plan mode still stays on until a human approves.
- The approve directive asks Hermes not to offer "Always" for a plan (`allow_permanent: false`). Current Hermes
  releases ignore the key and still show "Always", which behaves like once for plans; the prompt drops it once Hermes
  supports the key.
- Approval titles and plan-file names skip generic section labels such as Summary, Goal or Context, and the plan
  formats' own section names (Key changes, Changes, Decisions and assumptions, Steps).
  - They use the first heading that names the plan; if every heading is a label, the first line of text under them.
  - A `# comment` inside a fenced code block is no longer mistaken for a heading.
  - Plans without headings keep the file name as their title.
- `/planmode status` shows the Plan profile. Changing the profile, Plan style or Plan skill takes effect on the next
  turn, with no restart.
- CI now tests Hermes 0.21.6 and a current `main` (both on Python 3.14); the old `main` pin is dropped.
- The minimum Hermes is now 0.21.3 (`requires_hermes: ">=0.21.3"`), the oldest version CI tests. 0.21.2 was never
  tested, and its approval gate still prompts the user when `approvals.mode` is `off`, which the approvals-off path's
  tests do not expect.
- Docs: compatibility is stated in Hermes versions (0.21.3 for the classic CLI, 0.21.5 for gateway, TUI and Desktop),
  and 0.21.6 is recommended because enabling a tool plugin from Desktop, the TUI or the dashboard on 0.21.5 can
  replace the CLI tool set.

## 0.3.5 - 2026-10-07

- New setting **Plan style** (`plan_style`). `compact` is the default: it asks for a short, decision-complete plan
  (Goal; Decisions and assumptions; Changes per file; numbered Steps, each with its check; Validation commands) and
  replaces core `/plan`'s plan craft for that plan. `core` keeps Hermes' own craft (zero-context tasks, full code, a
  commit per step) with the 0.3.4 planning note, byte for byte.
- New setting **Allow commits** (`allow_commits`), off by default. While off, the planning note, the approval result,
  the executing-phase pointer and the typed-approve paths tell the agent not to commit or plan commit steps unless the
  user asked, so changes stay uncommitted for review. Values other than a boolean or a true/false word keep the default.
- New setting **Plan skill** (`plan_skill`), empty by default. The name of a skill (such as a durable planning
  contract) whose plan format the agent loads with `skill_view` and uses instead of Plan style. When plan mode has to
  block `skill_view` (`skills.inline_shell` on, or no readable skills config), Plan style is used instead, and the
  note tells the agent to use the Plan style format if the skill cannot be loaded.
- Fix: the short approval summary on Telegram, Slack and Discord keeps underscores, so `register_channel` and
  `__init__.py` no longer show as `registerchannel` and `init.py`. Underscore emphasis such as `_this_` now stays
  visible; `*` emphasis, backticks and strikethrough are still removed. Link stripping no longer slows down
  quadratically on a long line of unmatched `[`.
- When the agent turns plan mode on itself, the `plan_mode` result now carries the planning note (plan style or
  skill, and the commit rule), because that turn's planning note has already been sent.
- The short chat approval summary takes a Steps or Tasks section before a per-file Changes list, which the compact
  format writes first. A linked image such as a badge is unwrapped to its text.
- Why the defaults changed: an end-to-end comparison on eight coding tasks (same model and effort, one run per task,
  blind judge, decided by bars written before the runs) met every bar. Compact plans were about half as long, all
  eight tasks still passed their checks with no edit before approval, there were no commits (core made 1-4 per task),
  plan fit plus clarity scored 10.0 of 10 against 8.9, and median time per task fell from 194 s to 151 s. Token use was
  about the same. Set `plan_style: core` and `allow_commits: true` to keep the 0.3.4 behaviour.

## 0.3.4 - 2026-10-06

- When Hermes approves a submit automatically (yolo or `approvals.mode: off`), ask for human approval through the
  core `clarify` tool with the exact choices **Approve plan rev N** and **Keep planning**. Approval comes only
  from that question's correlated tool result, or typed `/planmode approve`; it starts implementation in the same
  turn. Keep planning and free text become review feedback. Denied, cancelled or unanswered human approval
  prompts do not trigger this fallback.
- Bind the question to its nonce, tool call, activation and unchanged plan digest. Block altered tagged questions
  and parallel approval questions; leave non-answers awaiting so the question can be re-asked. Typed commands,
  resubmission and session cleanup clear the question. Status and turn notes show approval asked in chat.
- Planning asks before it guesses. The planning note now sends the agent to explore first, then use `clarify` for
  each open preference or tradeoff that changes what gets built, and record any unanswered one as an assumption.
  In end-to-end testing on an ambiguous task, the previous wording asked 0 times in 4 attempts.
- An approval given through `clarify` carries the same todo-list instruction as the approval card, so progress is
  tracked and the executing phase closes when the work is done.

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
