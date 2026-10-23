# valesor-dev

Generic development machinery for Valesor repos: per-session roles (`/role`), path ownership, PR labels, the pre-PR review gate, PR comment handling, sprint skills, and base role charters. It is vendored into target repos with `make install`; targets enable roles by adding `targets/<t>/roles/<role>.md`.

## Install

Vendor it into a repo, so cloud sessions get it too (they don't load plugins): add a target under `targets/<t>/` and run `make install TARGET=<t> DEST=<repo>` from this repo, then commit the repo's `.claude/`. Outside a target repo, add the marketplace and install it:

```
/plugin install valesor-dev --marketplace valesordev/automate.valesordev.com
```

Don't do both in one repo, or its skills and hooks run twice.

**Updating from a tag.** A repo installed from a target updates itself with `make update-agents REF=<tag>`; the toolkit vendors the script into every install. See the [toolkit README](../../toolkit/README.md#update-agents).

## Roles

| Role | Assist level | Why | Skills |
|---|---|---|---|
| `pm` (target) | L2 Pair | The owner sets scope and priority; the agent plans and grooms | `pm-start-sprint`, `pm-close-sprint` |
| `architecture` (target) | L2 Pair | The owner makes design rulings; the agent drafts contracts and reviews | `arch-start-sprint` |
| `implementation` (target) | L2 Pair | The owner makes key decisions and merges; the agent builds and verifies | `impl-start-sprint` |
| `sre` (base) | L2 Pair (ops L3) | The owner sets SLOs and error-budget policy | `sre-start-sprint` |
| `product` (base) | L3 Delegate | The owner isn't the product owner; the agent owns vision, releases, and feature briefs, and they approve at the PR | `feature-brief`, `release-plan`, `release-review`, `product-triage` |
| `visual-designer` (base) | L2 Pair | The owner makes the identity decisions and selects every asset; the agent audits targets, directs and treats generated art, rebuilds marks as SVG, and derives tokens and brand docs | `brand-audit`, `art-direction`, `art-slot`, `brand-qa`, `design-start-sprint` |
| pre-PR reviewer (subagent spawned by `pre-pr`) | L2 Pair | It finds and proposes; the implementer fixes and owns the change | — |

Every role uses `role` (the owner only), `pre-pr`, `pr-comments`, and `project-sync`.

**The catalog in a session.** `/role help` (`role help`) prints the roles a repo defines as a table: role, aliases, assist level, summary, skills, and the `none` row. The assist level is the charter's own `Assist level: L<n>` line (an `extends` base's comes first). The summary is the role file's optional `summary:` key, one line on what the role produces. A role file without either shows `—`. `summary:` isn't inherited from a base, so each target's role file sets its own.

**Approvals in chat.** Every charter `role set` prints carries one rule for decisions the owner makes in conversation: record it in the doc that owns it before acting on it. If the role can't write that doc, it says so at once, names the owning role (`role owners <path>`), and offers to hand off with `story comment <ID> --to <role>`, quoting the decision. Work that rests on an approval that exists only in chat doesn't advance.

**Product and releases.** The `product` base role owns `docs/product/` (vision, `releases.md`, `features/FEAT-NN-*.md`, `reviews/`) in the repo its target puts it in. Delivery planners (`pm`, a content `producer`) keep their roadmaps, but every milestone names the release it delivers (`Release: R<n>`) and its player-visible change. Requests to product are plain issues labeled `product`; product asks a planner to change its plan with a `release-change` issue in the planner's repo. Create both labels once per repo.

**Brand kits.** The `visual-designer` base role runs in the sites monorepo (target `solo7-media`), where each brand lives at `brands/<slug>/` beside the sites that use it. The kits restarted from scratch on 2026-10-07, and the old `brand-kit` target is retired. Each brand starts from a frozen design target: `/brand-audit <slug>` sorts what the target shows into core identity, candidate motif, or hero-specific, and the owner approves those classifications at PR review before any tokens exist. Then come the brand docs, tokens, and art. Imprints ship with generated artwork, marks included. `/art-direction` keeps the illustration system (stable rules separate from per-image prompts, plus composition types) and renders the ChatGPT project instructions. `/art-slot` takes one slot from prompts to an installed, `make`-built source with provenance. `/brand-qa` checks a built page against the brand's `qa.md`, and `implementation` runs it too. visual-designer is a sprint lane (`/design-start-sprint`). Builders ask brand questions with `story comment --to visual-designer`, and the answer is a brand doc change, never a value picked in code.

**Role sessions work in a worktree.** `/role <role>` moves the session into its own worktree, so the owner keeps working in the checkout they launched from. `role set` names it `role-<role>-<first 8 characters of the session id>`, records it with the session, and adds `.claude/worktrees/` to the repo's local exclude. The skill then calls Claude Code's EnterWorktree, which branches it from `origin/main` under `.claude/worktrees/`. The role's own branch is created there. There's no worktree for `/role none`, a cloud session (`CLAUDE_CODE_REMOTE`), or a coordinator lane, and a role change inside a worktree keeps the one it has. On resume or compaction, the SessionStart hook says to re-enter the worktree when the session's directory isn't in it. Session state lives in the git common dir, so the role, the review gate and the hooks behave the same there. The coordinator's `clean` and `handoff` never touch `role-*` worktrees. At the session's end, Claude Code asks whether to keep or remove the worktree.

## Tools

A role can name the CLIs it uses for an outside system: `tools: [gcx]` in its frontmatter, or in `_repo.md` for every role. The first is `gcx`, for everything in Grafana Cloud. A tool definition is `tools/<tool>.md` in a plugin (generic, vendored from a pin like base roles) or in `targets/<t>/tools/` (the target's own, or `extends: <tool>` to add notes and `instead_of` patterns to a plugin's). Its frontmatter:

| Key | Meaning |
|---|---|
| `tool`, `kind`, `domain` | Its name (the file's), `cli`, and what it is the tool for (`Grafana Cloud`). |
| `check` | A command that succeeds when the tool is installed (`gcx --version`), run without a shell. |
| `catalog` | The command that lists its commands (`gcx commands -o json --flat`: JSON objects with `full_path`, or one path per line). Without one, write the snapshot by hand. |
| `read_verbs`, `always_ask` | Which commands are reads: those whose last word matches a `read_verbs` pattern (`list`, `get-*`), except any under `always_ask` (`gcx api`). Everything else asks. |
| `instead_of` | `commands` (programs that bypass the tool: `mimirtool`, `logcli`), `bash` (regexes over a command: `curl` against `*.grafana.net`), and `url` (regexes over WebFetch and browser URLs). |
| `credentials` | Where its credentials live, never their values. |

The body is the tool's usage notes for this repo's agents. It points at the tool's own agent docs (`gcx help-tree`, `gcx agent skills`) rather than copying them.

**Permissions are generated.** Permission rules match a command's start, and gcx puts the verb last (`gcx dashboards list`), so the rules name every command. `make tool-snapshot TOOL=<tool>` needs the CLI: it runs `catalog` and writes `tools/<tool>.commands.txt`, the version and then one command path per line. `make build` doesn't need it: it derives `tools/<tool>.permissions.json` from the snapshot and the definition, and `make check-render` fails when that is stale. So a CLI upgrade is two reviewable diffs, the new commands and the rules they produce. A definition with `extends` can't change the classification: the definition with the snapshot owns it.

**What install does.** For each tool the target names, install writes `.claude/tools/<tool>.md` (the notes) and `.claude/tools/<tool>.json` (domain, check, `instead_of`, and the rules), and merges the rules into `.claude/settings.json`: reads to `allow`, the rest to `ask`. Permission rules are session-wide, so every role gets the reads. An `ask` rule wins over an allow, and it prompts in auto mode too, so a write to the outside system always reaches the owner.

**What a session sees.** The charter (`role set`, SessionStart) lists the session's tools, repo-wide then the role's own: `- Grafana Cloud: gcx (.claude/tools/gcx.md)`. When a tool's `check` fails, the line says `NOT AVAILABLE` and tells the agent to raise it with the owner rather than work around it.

**Advice, not enforcement.** When a Bash command runs one of a session's tools' `instead_of.commands` (in any part of a chain, inside `$(...)`, behind assignments and wrappers) or matches `instead_of.bash`, or a WebFetch or browser navigation matches `instead_of.url`, the hooks add a note to the model's context: use gcx, or say why it can't. They make no permission decision, so the rules decide as they would anyway. A role that doesn't list the tool gets no note. A missing or unreadable `.claude/tools/<tool>.json` gives no note and never fails the hook.

**Lanes.** A lane gets its tools' read rules in its `--allowedTools`; their `ask` rules are denied under `dontAsk`, so a lane never writes to an outside system. The plan refuses a role whose tool rule `lane_allow` would refuse. `coordinator run` runs each lane tool's `check` as the lane user (`sudo -iu <user>`) and warns when it fails. The lane user needs the tool and its own login (`sudo -iu lanes gcx login`), never yours.

MCP servers as tools, and hard denies for `instead_of`, aren't built yet.

## Hooks

| Event | Command | Effect |
|---|---|---|
| `SessionStart` | `role hook-session-start` | Re-injects the charter (and points back to the session's worktree), or asks for `/role` |
| `PreToolUse` on file tools | `role hook-pre-tool-use` | Enforces the role's `writes` ownership |
| `PreToolUse` on `Bash` | `role hook-pre-bash` | Git and shell guards (below): deny in every mode. `gh pr create` needs `--label role:<role>`. Review gate (below): deny in a role session, ask under `/role none`, deny with no role set. A note when the command bypasses one of the session's tools (Tools, above) |
| `PreToolUse` on `WebFetch` and browser navigation (`mcp__.*navigate`) | `role hook-pre-url` | A note when the URL is one a session's tool covers; never a decision |

## Project board

`bin/project-mirror` mirrors the story files on `origin/main` into an org GitHub Project, one way (repos with `stories: github` are never mirrored). It reads `docs/stories/*.md`, `docs/epics/*.md`, `docs/sprints/SPRINT-*.md`, and `docs/roadmap.md`, and writes issues, milestones, sub-issues, blocked-by links, and the project fields (Status, Lane, Size, Risk, Component, Sprint, Plan, Rank, Story ID).

A target opts in with `_repo.md` frontmatter:

```yaml
project: Example Game   # org Project title
story_prefix: GS          # GS-CLI-003
github_repo: owner/name   # optional; default is the origin remote
```

`/project-sync` syncs the board and reports the sprint: done/total, open stories, and what the end of the sprint depends on. `pm-start-sprint` and `pm-close-sprint` sync during preflight. After a PM sprint PR merges, `/project-sync` is the final sync for that boundary: it records the close-out and moves the board to the new sprint. Board edits made on GitHub are overwritten, so change the story file instead. It needs `gh` with the `project` scope.

**Cutover.** `project-mirror --cutover --ref <tag>` (`make cutover DEST=…`) hands a mirrored repo's board to GitHub. It reads the files at a pushed tag (`stories-final`) and stops unless the board matches them. Then it:

- replaces the single-select `Sprint` with a number field;
- creates the `SPRINT-NN — <goal>` issues: closed ones link their plan file, and an open one carries its plan;
- strips the mirror notice from every issue;
- comments on each open story with links to its `docs/feedback/` files.

Each step skips what's done, so a rerun is safe. Afterward the mirror refuses to run, because issues without the notice would be duplicated. The repo's target then sets `stories: github` and `sprints: project`.

## Stories (`bin/story`)

For a repo with a Project (`project:` in `_repo.md`), `bin/story` is how role sessions change stories. Writes need `stories: github` in `_repo.md`, set at the repo's cutover from story files. Before that, the mirror owns the board and `story` only reads. It works over REST only, because Claude Code cloud sessions block GraphQL:

| Command | Does | Allowed by |
|---|---|---|
| `story show <ID> [--comments N]`, `story list [--plan/--sprint/--cycle/--status/--lane/--prefix]` | read (`--prefix` lists another repo's stories on a shared Project) | anyone |
| `story create --comp <code> --title … --lane … --size … --risk … --component … --body-file F [--epic EPIC-NN] [--depends ID,…]` | new issue on the Project at `draft`, with its fields, epic sub-issue, and blocked-by links | `new>draft` in `transitions:` |
| `story start <ID>` / `story status <ID> <status> [--note …]` | moves Status and posts `**Status (<role>):** a → b`. Moving to `done` closes the issue. Refused, listing every gap, while a record the move needs is missing | `transitions:` (`from>to`, `from>to@lane` for the role's own Lane, or `from>to@<lane>` for a named lane such as `@director`) |
| `story check <ID> <status>` | read: the records the move still needs and the role that owes each (exit 1 if any), and whether this session's role may make it | anyone |
| `story set <ID> Field=value …` | Sprint, Cycle, Plan, Rank, Lane, Size, Risk, Component | `story_edits:` |
| `story body <ID> --body-file F` | replaces the contract (issue body) | `story_edits: [body]` |
| `story depends <ID> [--add ID,…] [--remove ID,…] [--note …]` | adds or removes the story's blocked-by links and posts `**Depends (<role>):** blocked by +A, -B`. The story is this repo's; a blocker can be any story on the Project. Re-adding or removing a link that isn't there writes nothing | `story_edits: [depends]` |
| `story comment <ID> (--to ROLE \| --record NAME) (--body … \| --body-file F)` | `**For <role>:**` or `**<record> (<role>):**` comment | any role |
| `coordinator comment <ID> --to ROLE (--body … \| --body-file F)` | `**For <role>:**` comment stamped `from: coordinator` | the role-free dispatcher session, `/role none`, or a terminal; never a lane or a role session |
| `coordinator file-issues <run-id> [--lane NAME] [--apply]` | the run's drafted config-failure issues, shown; with `--apply`, filed in the installing repo | the owner; never a lane |
| `story merged <PR>` | the story-merge Action: applies a merged PR's `Story:` / `Story-Done:` lines | the PR's single `role:<role>` label, with that role's `transitions:` plus `on_merge:` |

The session's role comes from `--sid` or `$CLAUDE_SESSION_ID`, which the SessionStart hook exports through `CLAUDE_ENV_FILE`. `/role none` may do anything, and with no role set, writes are refused. The Bash hook denies raw writes in role sessions and asks under `/role none`:
- `gh issue edit/close/reopen/delete/transfer` on story issues. It checks the issue's title, so other issues are free.
- non-read `gh project` commands
- `gh api` writes to Projects, story issues, sub-issues, or dependencies, and GraphQL mutations

Like the review gate, it's a parser and guards what it can see.

**Status at merge.** `workflows/story-merge.yml` is vendored to `.github/workflows/` and runs `story merged` when a PR merges, using a token from the `valesordev-story-bot` GitHub App (org variable `STORY_APP_ID`, org secret `STORY_APP_PRIVATE_KEY`). It reads the PR body and commit messages:
- `Story: <ID>` moves the story `in-progress → review`, but only when the PR's role is the story's lane. Other roles' PRs, such as an architecture §8 review, only reference it.
- `Story-Done: <ID>` moves it `review → done` if the role may.
- Moves listed in a role's `on_merge:` happen only here, never from a session, so `review` means the work is on `main`.
- A lane's own story that wasn't `in-progress`, or a `Story-Done:` the role can't make, fails the run after the rest are applied. The red run is the status-defect signal.
- Only merges into the default branch count: the job condition checks it, and `story merged` checks it again.
- Repos without `stories: github`, including repos with no Project, skip green, and the job doesn't mint an App token.

**Sprints as issues.** With `sprints: project` in `_repo.md` (set at a repo's cutover), a sprint is an issue in the repo:
- It's titled `SPRINT-NN — <goal>`. Its body holds the goal and demo goal.
- It carries the label `sprint` plus `sprint:planned` or `sprint:active`. A closed issue is a closed sprint.
- `sprint-state` reads these issues instead of `docs/sprints/` and applies the same rules: exactly one open sprint, and only the highest-numbered one.
- Roles with `Sprint` in `story_edits` change sprints with `story sprint plan|activate|close|body <N>`, in the order close N, then plan N+1, then activate it.
- The story guard covers sprint issues.

**Required records.** A role's `records: [from>to: Name, …]` lists the records it owes before a move, for example SRE's `records: [draft>ready: Observability review, review>done: §8 instrumentation]` and architecture's `records: [review>done: §8]`. A record is a `**<Name> (<role>):**` comment, as `story comment <ID> --record <Name>` writes it, posted since the story last entered the move's from-status, so a rework round needs fresh records. The owner's record (`(owner)`, from `/role none`) stands in for any role's. `story status` and `story merged` (`Story-Done:`) refuse a move with any record missing and list them all, each with its role. `/role none` moves anyway and prints a warning. `story check` is the read-only version. The gate checks that a record exists, not what it says: a failed §8 is recorded and the story stays at `review` by the reviewer's judgment. Roles that aren't enabled owe nothing, so a target without SRE doesn't wait on SRE.
- Stories record their sprint in the Project's `Sprint` field. As a number field it takes `5`, `05` or `SPRINT-05` in `story set` and `story list --sprint`. Number fields need no options, and options can't be created over REST.
- Demo and close-out docs stay in files.

**Role layer.** The PM, architecture, SRE, and implementation sprint skills work the GitHub board. They stop unless the repo has switched over (`stories: github` and `sprints: project`). The protocol they share (where contracts, feedback, records, and sprints live, and each story's life) is `skills/role/references/stories-on-github.md`.

**Shared Projects.** Several repos can put stories on one Project (a game's server `GS-*` and content `GC-*`). Each repo sets its own `story_prefix`, `github_repo`, and `story_components` (the `--comp` codes; default `SRV, CLI, INF, CLT`). Reads see the whole board, so `show` and `--depends` work across repos. Writes refuse a story with another prefix or in another repository. `project-mirror` only touches its own repo's issues, so a mirrored repo and a GitHub-native one can share a board.

`sprint-state --name CYCLE --dir docs/planning/cycles` reads another series of plan files the same way (a content repo's cycles).

## Coordinator (`bin/coordinator`, `/sprint-run`)

**Handing a role something from the dispatcher.** The session that runs the coordinator stays role-free, and with no role `story` refuses every write. `coordinator comment <ID> --to <role> --body-file F` posts the `**For <role>:**` comment `story comment --to` would, stamped `from: coordinator`. It refuses inside a lane (`COORDINATOR_LANE`) and in a session holding any role but `none`, which post as themselves with `story comment`, so no lane or role can post as the coordinator. The role answers `--to owner`.

**When the agent setup stops a lane.** Some lanes fail because of the setup, not their story: a hook or the permission mode denies what the charter or a skill asks, a skill step can't work in a lane, or the lane's environment lacks a tool. Such a lane ends with `LANE RESULT: blocked: config: <what stopped it>`, and its prompt tells it to. The run then drafts an issue for it, and does the same for any lane that was refused a tool call and didn't finish. A lane that finished despite a denial, or one blocked by its story, gets nothing. The draft fills the *Skill refinement* fields from what the run saw:
- the result line;
- each refused command or file;
- the lane's last message, with the worktree, scratch, checkout and home paths replaced by placeholders, and every `$AUTOMATE_DENYLIST` term (the file `check-public` reads) replaced by `[redacted]`.

Expected and Proposed are left for you. Drafts go in `runs/<id>/issues/`, one per fingerprint (the role and what was refused), and the report lists them. Nothing leaves the machine until you say so. `coordinator file-issues <run-id>` prints them for review, and warns when no denylist is set, since a lane's words can name people. `--apply` files them in the repo the agents were installed from (`repo` in `.claude/.automate.json`), with `kind:bug`, `source:session` and `plugin:<target>`, each only where that repo has the label. An open issue that carries the same fingerprint gets a "seen again" comment instead of a duplicate. Filed links are added to the run's report.

`coordinator plan|run|status|handoff|clean|comment` runs a sprint's role lanes in parallel. It is a script, not a model. It reads the board (`sprint-state`, the Project items, blocked-by links) and decides which lanes can start:

A role runs as a lane when its charter says so (`sprint_run:` in the frontmatter); implementation, SRE and PM have built-in defaults, so a repo needs nothing for them. A charter can set:

| Key | Meaning |
|---|---|
| `sprint_run: story` | One lane per story the role owns (`lane: <role>`): `ready`, or `in-progress` with no live lane and no open PR (carryover first), blockers at `review` or later, Rank order, never two stories of one role in one Component, at most `--max-lanes` (3) at once. |
| `sprint_run: session` | One lane per role, started when its trigger holds and the board has changed since its last finished lane. |
| `start_skill:` | The skill the lane follows (`impl-start-sprint`, `design-start-sprint`, …). |
| `inbox: true` | Story roles: a lane that only answers `**For <role>:**` comments, ahead of the backlog. A question is open until the role replies to the asker after it (`story comment` stamps its author in a hidden `<!-- from: <role> -->` marker; a question from `coordinator comment` is the owner's, so the reply goes `--to owner`; older comments fall back to "a later handoff to someone else"); `done` stories don't count. While one is open, the stories that asked wait, and so does the role's own backlog until the lane has run. The lane is done only when the board shows its questions answered, not on its word. Decisions that are the owner's (direction, palette, type, marks, voice) are not the lane's to make: its prompt has it reply `--to owner` with one recommendation and leave the question open, and an open question is not retried until a comment on it changes. Each inbox lane has its own worktree. |
| `approval:` | What the owner still decides once the lane's PR is open. The report lists it under *Needs attention* next to the PR; the lane is still done. |
| `watch_label:` | Session roles: the lane runs when issues with this label are open (and `watch_pr_section:`, when a merged `role:<role>` PR has that heading in its body and fewer `release-change` issues link its URL than it owes: one per bullet, except a bullet addressed to a role of this repo that isn't a delivery planner (`pm`, `producer`); a section that says "None" or has no bullets owes none). Each queued item runs once: the coordinator remembers what it looked like after the lane, so an issue the lane commented on and left open comes back only when someone comments again. After a lane the queue is re-read: an item left untouched is not retried and an issue left open is not forgotten; both stay in every plan and report as waiting on the owner (a PR as `merged; release-change handoff missing (N of M)`). A lane that crashed or timed out dequeues nothing. `triaged <n>` must match the queue size. A gh failure (or 100 or more open issues) skips that role, not the plan. The lane is done only when the re-read shows every item commented on, closed, or handed off (GitHub's search index is given a few seconds), and its worktree is clean. Each watch or inbox lane has its own worktree, removed when the lane ends clean with nothing to keep. |

Targets such as `solo7-media` also run `product` this way (`watch_label: product`, start skill `product-triage`: answer, fold into a brief, or close each issue, open owed `release-change` handoffs, PR the `docs/product/` change with a "Decided this session" list).

Built in: **implementation** (story lanes), **architecture** (one lane running `/arch-start-sprint` when the sprint has drafts to contract-review, stories at review for §8, architecture stories to build, or questions addressed to it, but never alongside an SRE lane: it acts on SRE's reviews, so it waits for SRE's turn on the board; it makes its board moves itself, `draft → ready` and `review → done`, with its reviews recorded as story comments, and its ADR and spec changes come to the owner as `arch/` PRs), **sre** (one lane running `/sre-start-sprint` when the sprint has drafts to review, stories at review to verify, or SRE stories to build), **pm** (one lane running `/pm-start-sprint` when `sprint-state` says `first`, `closed`, or `planned`; a finished sprint is reported, not closed, unless `--pm-close`). `solo7-media` sets `visual-designer` as a story lane with an inbox: brand docs, tokens and art slots are its stories, and its PR is done while the audit, the candidate selection and the mark approvals are still the owner's. Without `--roles`, every role with a charter that runs as a lane is planned (`sprint_run: none` opts a role out; a value the coordinator can't run is reported in the plan under the owner rather than stopping it). `inbox: true` is for story roles only.

Every role that works in the repo is meant to run as a lane, so its commits are pushed by the app and the owner's name is only on commits they make, and their approval on its PRs counts (`main` wants an approval from someone other than the last pusher). Every session lane (architecture, SRE, PM, a watching or inbox role) gets its own worktree, `<role>-<run>`, removed when it ends clean: an earlier lane's open PR keeps its commits in its own worktree and never stops the next lane. A session lane whose work is on the board, not in a PR (architecture readying stories, SRE's reviews), ends with `LANE RESULT: recorded <n>`, which counts only if a sprint story (other than a story lane's own) changed on the board while it ran. Stories waiting on a blocker, on a role that didn't run, or on a role without a charter or a `sprint_run:` are listed with what they wait on. Only roles with a charter in `.claude/roles/` can run.

**`person-only` stories.** A story that needs what a lane never has (docker, kind or a cluster, upstream downloads, credentials) carries the `person-only` label. The coordinator never plans it (it is listed as waiting on the owner), a session lane's own `person-only` story doesn't start that lane, `story show` prints the label, and `story start`/`status` refuse to move it inside a lane. A lane that reaches such a story without the label comments `--to owner` that it should have it. Each repo needs the label once (`gh label create person-only`).

Each lane is a separate `claude -p` session. The coordinator picks its session id, records the role for it with `role set`, adds a detached worktree at `refs/remotes/origin/main` under `.claude/worktrees/`, and starts it there, so the ownership, review-gate and story-guard hooks bind it as they bind an interactive session. The hooks it runs come from a copy of `bin/role` in the run's directory under `<git-common-dir>/claude-coordinator/` (passed with `--settings`), and they judge the lane by a copy of the main checkout's `.claude/roles/` made at the same time (branch prefixes, `github_repo`, `writes`), not by the lane's checkout: a hook whose file is missing doesn't run, and that allows, so the hooks must live where a lane can't move or edit them. The worktree's vendored hooks (`--project` in `.claude/settings.json`) step aside in a lane: they are the branch's own copy, older on a branch cut before a fix, and a deny from either copy wins, so an old branch could never `git rebase origin/main` onto the fix. A copy older than the step-aside can't stand aside, so before a lane resumes a branch that is behind origin/main, the coordinator catches it up itself: `git rebase origin/main` while the branch is unpushed, `git merge --no-edit origin/main` once it is pushed. The lane then starts on the current `.claude/`. A branch that conflicts, or a worktree with uncommitted changes, isn't touched: the lane isn't started, and the report says it needs the owner. Lanes run in `dontAsk` mode: what isn't allowed is denied, never prompted, so a lane never waits on a person.

Each target's settings make `coordinator run` and `clean` `ask` rules and `plan`/`status` `allow` rules: starting lanes is always a permission prompt, auto mode included (its classifier otherwise refuses a command that starts unattended agents), and a lane, which has no one to ask, can never start one (the coordinator also denies it outright).

Before it starts anything, `run --apply` checks two things and refuses otherwise:

- **Lanes act as their own GitHub App.** `~/.config/claude-coordinator/lane-app.json` (or the file `COORDINATOR_APP_CONFIG` names) holds the app's `app_id`, and its private key sits beside it as `lane-app.pem`, mode 600. The app must be installed on the repo and hold no more than lanes need: Contents, Pull requests and Issues read and write, Commit statuses, Checks and Actions read, Metadata, and the organization's Projects read and write. Any other permission (Administration, Workflows, …) refuses the run, because the key could mint a token past main's rules. GitHub never lets an author approve their own pull request, so only a lane PR opened by another identity can wait on your approval, and an app takes no seat. The coordinator signs the app's login with `openssl` and mints an installation token narrowed to this repo, kept in `runs/<id>/auth/token` (mode 600). Lanes reach it through a `gh` and a git credential helper in `runs/<id>/auth/bin` (first on their `PATH`) that read the file on every call. git finds that helper, and the rewrite of GitHub SSH URLs to HTTPS, in `runs/<id>/auth/gitconfig`. Their environment carries only `include.path` to it, because a tool that drops empty variables when it runs git (buf does) would otherwise break git. Their `GH_TOKEN`/`GITHUB_TOKEN` are removed, and `GH_CONFIG_DIR` is an empty directory, so a gh that misses the shim has no login of yours. Pushes go over HTTPS as `<app>[bot]`, never over your SSH key, so the latest push on a lane PR is the app's and your approval counts. Commits are still signed with your key. Installation tokens last an hour: the run renews the file while the token has under 5 minutes left, and deletes it when the run ends. The private key never enters a lane's environment, and a lane that runs as its own OS user can't read it. The coordinator itself (board reads, `codex-review`, the checks below) keeps using your own gh auth. `--person-token` starts lanes as you anyway.
- **main takes only reviewed pull requests.** A ruleset or branch protection on `main` must require a pull request with an approving review, and the approval must cover the latest push (`require_last_push_approval`, or stale approvals dismissed). A ruleset may let only admins (organization admin, or the repo admin role) bypass it, and only by pull request: you merge your own PRs without an approval, the bot can't merge anything you haven't approved, and nobody pushes to `main` directly. `--unprotected-main` starts lanes anyway.
- **Lanes run as their own OS user.** `~/.config/claude-coordinator/lane-user.json` (or the file `COORDINATOR_LANE_USER_CONFIG` names) holds `{"user": "lanes"}`, and optionally `home`, `repos` and `runs`, which default to the user's home, `<home>/repos` and `<home>/runs`. The run refuses unless:
  - the user exists and is neither root nor you;
  - `sudo -n -u <user>` works without a password, and the user itself can't use sudo;
  - `runs/` is yours, belongs to the user's group, and has mode 2750, so the user can read it and never write it;
  - the user can write `repos/`;
  - the user can't read the app's key or your checkout.

  The lane user needs the app, because it has no GitHub login of its own. Lanes then work in worktrees of that user's own clone (`<repos>/<owner>--<repo>`, made from your checkout's `origin` over HTTPS as the app), beside it in `<owner>--<repo>.worktrees/`. Every command that touches them goes through `sudo -n -u <user>`, so your process never reads their files.
  - **What the lane reads from you:** its hooks, the role policy, a small `lanectl` helper, the token, and its start environment are in `<runs>/<repo>-<run>/`, group-readable and never writable by the lane. The token and the environment files are deleted when the run ends.
  - **The lane's environment:** `HOME`, `USER` and `PATH` are the lane user's own (`PATH` from its login shell). Your `CLAUDE_CONFIG_DIR`, `SSH_AUTH_SOCK`, `TMPDIR` and `XDG_*` are dropped.
  - **Stopping a lane:** the coordinator signals `sudo`, and runs `sudo -u <user> kill` for the lane's own processes.
  - **Commits** are signed with the lane user's own signing key, which is registered on your account for signing only.
  - **Trust:** Claude Code ignores a repo's permission rules until its trust dialog is accepted, and no one is there to accept it in a lane. The coordinator accepts it once for the lane user's clone, in that user's `~/.claude.json`.
  - **UID:** the run refuses if anything you didn't start through sudo already runs as the lane user's UID, such as a container whose image uses the same number. Lanes could read those processes' environment and signal them. Give the lane user a UID nothing else uses.

  `--same-os-user` starts lanes as you anyway, in worktrees under `.claude/worktrees/`.
- **Lanes are captured by Grafana Agent Observability** when you use [agento11y](https://github.com/grafana/agento11y). Each lane gets agento11y's settings, from your `~/.config/agento11y/config.env` with your environment over it, but never its credentials: no variable whose name contains `TOKEN`, `AUTH`, `HEADER`, `KEY`, `SECRET` or `PASSWORD`. Its `AGENTO11Y_TAGS` keep the repo's `project` and `team` (from its `.envrc`, or else from `github_repo`), set `agent_role` to the lane's role, and add `launcher=coordinator`.
  - **A lane user reports with its own login.** agento11y's local mode is per user: a lane pointed at your local mode starts a daemon of its own, which never forwards. So lanes don't get your routing settings (`*_LOCAL`, `*_LOCAL_FORWARD`, `*_ENDPOINT`, `*_STACK_URL`), and the lane user's own `config.env` sends to Grafana Cloud with a write-only token.
  - **Warnings, not refusals:** the run warns, but still starts, when the lane user has no `agento11y`, no plugin, no token of its own, or is set to local mode. Otherwise it runs the lane user's `agento11y doctor` and warns with each check that fails (a token Grafana's OTLP gateway rejects, say).
  - **Cleanup:** after a run the coordinator stops whatever the lanes left running as the lane user, such as a daemon a hook started.
- **You're asked to review each lane PR.** Once a lane's PR is open, the coordinator has the app request your review (the login your gh uses), once per lane. GitHub won't take a review request of a PR's author, and the app is the author, so it's the app that asks. For lanes that run as you (`--person-token`) there is no request: the PR is yours.
- **Codex reviews lane PRs at your request.** Codex reviews only for someone with a Codex account, and the lanes' app has none: it neither reviews the app's PRs on its own nor answers the app's `@codex review`. So a lane's `codex-review wait` only waits, and the coordinator posts `@codex review` with your gh login, once for each new head commit of a lane's open PR.

**Setting up the lane app.** Once per organization:

1. As an organization owner: Settings → Developer settings → GitHub Apps → New GitHub App, owned by the organization. Name it e.g. `valesordev-lanes` (its PRs show as `valesordev-lanes[bot]`). Homepage: the org's URL. Turn **Webhook** off. Repository permissions: **Contents**, **Pull requests**, **Issues** read and write; **Commit statuses**, **Checks**, **Actions** read; **Metadata** read (required). Organization permissions: **Projects** read and write. Everything else, **Administration** and **Workflows** included, stays at no access. *Only on this account.*
2. Note the **App ID**. Generate a private key; the browser downloads a `.pem`.
3. Install the app on the organization, *Only select repositories*: the repos lanes run in.
4. Save it for the coordinator:
   `install -d -m 700 ~/.config/claude-coordinator && install -m 600 ~/Downloads/<app>.*.private-key.pem ~/.config/claude-coordinator/lane-app.pem && echo '{"app_id": <App ID>}' > ~/.config/claude-coordinator/lane-app.json`, then delete the download. `run --apply` prints `lanes run as <app>[bot]` once it can mint a token.
5. Apply the target's ruleset to `main` (`targets/<t>/main-ruleset.json` in automate.valesordev.com): `gh api -X POST repos/<owner>/<repo>/rulesets --input targets/<t>/main-ruleset.json`, or `-X PUT repos/<owner>/<repo>/rulesets/<id>` to replace an existing one. It lets only admins bypass, and only by pull request, so the app is never on its bypass list.

The key doesn't expire: if it leaks, revoke it on the app's page and generate another.

**Setting up the lane user.** Once per machine (Linux; the commands are for Arch):

1. `sudo useradd --create-home --home-dir /var/lib/lanes lanes`, then `sudo chmod 750 /var/lib/lanes` and `sudo usermod -aG lanes $USER`.
2. Lanes' directories:
   - `sudo install -d -o lanes -g lanes -m 2750 /var/lib/lanes/repos`
   - `sudo install -d -o $USER -g lanes -m 2750 /var/lib/lanes/runs`
3. Sudo: add `<you> ALL=(lanes) NOPASSWD: ALL` with `sudo visudo -f /etc/sudoers.d/lanes`. `lanes` itself gets no sudo.
4. Keep your files out of its reach. Your home is usually mode 700 already; checkouts elsewhere need a parent the user can't enter, e.g. `sudo chmod 750 /data/dev`.
5. Claude: run `sudo -iu lanes claude` and `/login`.
6. Signing key:
   - `sudo -u lanes ssh-keygen -t ed25519 -N "" -C valesordev-lanes-signing -f /var/lib/lanes/.ssh/lane_signing`
   - Its git config: `user.name`/`user.email` as yours, `gpg.format ssh`, `user.signingkey` the `.pub`, `commit.gpgsign true`.
   - Add the `.pub` to your GitHub account as a **signing** key (`gh ssh-key add --type signing`, after `gh auth refresh -s admin:ssh_signing_key`).
   - No GnuPG daemons: git calls `gpg` to check GitHub's GPG-signed merge commits, and GnuPG 2.4 then leaves `keyboxd` running as the user, which the preflight refuses (nothing else may run as its UID). Write `no-autostart` to `~/.gnupg/common.conf` (replacing `use-keyboxd`) and to `~/.gnupg/gpg.conf`, as the user.
7. Tools: the lane user's PATH comes from its login shell (`~/.profile`, read from `~/.bash_profile`). Install the repos' toolchains for it: Go tools with `go install`, Node with nvm, its `bin` named in `~/.profile`. After the first run makes a clone, run a repo's own setup as the user, e.g. `sudo -iu lanes make -C /var/lib/lanes/repos/<owner>--<repo> bootstrap`. A setup that looks for a signing key in the clone's own config (the server's does) needs it set there first: `sudo -u lanes git -C <clone> config --local user.signingkey /var/lib/lanes/.ssh/lane_signing.pub`, and likewise `gpg.format ssh` and `commit.gpgsign true`.
8. agento11y, if you use it: install the binary for the lane user, log it in to Grafana Cloud with a token of its own, and register the plugin in its Claude.
   - In Grafana Cloud, make an access policy with agento11y's write scopes only (`sigil:write` for conversations, `metrics:write` and `traces:write` for its OTLP analytics), and a token for it. Without the last two, conversations arrive and the OTLP gateway answers 401.
   - `sudo -iu lanes sh -c 'curl -fsSL https://raw.githubusercontent.com/grafana/agento11y/main/plugins/agento11y/scripts/install.sh | sh'`
   - `sudo -iu lanes agento11y login`: choose Grafana Cloud, not local, and paste the stack's settings with that token.
   - `sudo -iu lanes agento11y claude install`
9. `echo '{"user": "lanes"}' > ~/.config/claude-coordinator/lane-user.json`.

**What a lane may run.** The coordinator passes `--allowedTools` to lane sessions only (it widens nothing for a person's session): file edits, `/pre-pr`'s reviewer subagent, the repo's `role require/show/owners/check-cmd/reviewed`, `story`, `sprint-state` and `codex-review`, the repo's check command, git and gh by verb: `status/diff/log/show`, `fetch [origin]`, `add/commit/switch/checkout`, `push`, `grep`, `ls-tree`, `rebase`, `merge`, `worktree add/remove/list/prune`, `gh pr create/view/list/diff/checks/comment`, `gh issue view`, `gh run list/view` (CI evidence), `gh api`, the readers `grep`, `head`, `tail`, `wc`, `ls` and `diff`, and `echo` (whose redirections the hooks judge). The list matches each part of a chained command, so one unlisted part (`; echo "exit $?"`) denies the whole call: the lane prompt asks for one command per call, lets a denied chain be re-run once as its separate commands, and has the check run as it is, piped to `tail`, never redirected to a file. These are prefix matches, which a flag in another order defeats (`git push -q -u origin x` against `git push -u origin:*`), so they name the verb and the hooks below judge the flags, the remote, the branch and the files a reader names. A role with `watch_label` adds `gh issue list/comment/close/create`. Tools a repo installs into a gitignored directory (`make bootstrap` → `bin/`) aren't in a fresh worktree: `lane_path: [bin/]` in `_repo.md` puts those directories of the clone the worktrees come from (the lane user's, or yours with `--same-os-user`) on lanes' `PATH`, after the gh/git auth shim; bootstrap that clone once as the lane user. A charter can add `lane_allow: [Bash(<command> <verb>:*)]` for its build and test commands: one rule per entry, no options, shell syntax, wildcards, quotes or `..`, never a shell, wrapper, interpreter, file tool, `git`, `gh`, or a repo tool. The list *adds* to the permissions the user's and the repo's settings already grant; it doesn't remove them, so a repo that allows more allows its lanes more. `--no-lane-allow` starts lanes with only those settings. Don't use it unattended: Claude Code's own command check (which refuses env-var prefixes, `$(...)`, redirections and `cd` outside the worktree at the top level) and the hooks below are two layers, and the list is what keeps a bare `cat`, `curl` or `python3` out of a lane.

A lane's environment is an allowlist (`PATH`, `HOME`, `SSH_AUTH_SOCK`, `GH_TOKEN` (replaced by the app's when there is one), `ANTHROPIC_*`, `CLAUDE_CODE_*`, `LC_*`, `XDG_*`, proxies and CA bundles, and the like), not the coordinator's whole one; `COORDINATOR_ENV_PASS=A,B` adds variables by name, but never `GIT_*`, `CLAUDE_*`, `COORDINATOR_*`, `GH_CONFIG_DIR`, `PATH`, `HOME`, or the shell's own (`ENV`, `BASH_*`, `LD_*`, `PAGER`, `EDITOR`).

**What a lane may never do.** The list above is the outer layer. The inner one is the `bin/role` hooks, which judge the tokenized command when `COORDINATOR_LANE` is set (the coordinator sets it; the hook inherits it from the session, not from a command a lane runs, and a command that assigns or unsets `CLAUDE_*`/`COORDINATOR_*` is refused). A hook that runs and crashes denies, and so does one that can't read its input; a hook whose file is missing never runs, which is why the coordinator serves them from outside the checkout. A command over 8,000 characters or with more than 12 git/gh/tool commands is refused, because a hook that times out allows. A checkout without `.claude/roles` or `.claude/bin/role` is refused everything.

- **Shell.** No `NAME=value` prefix or wrapper (`env`, `timeout`, `nice`, `xargs`, `setsid`, …) in front of git, gh or a repo tool; a bare assignment only to a name the lane's environment doesn't have (`WT=…` is fine, `HOME=…` is not). No `export`, `unset`, `source`, `eval`, `declare` or `trap`. Inside `$(...)`, backticks and `<(...)`, only git, gh, the repo's tools, `mktemp`, `date`, `pwd`, `echo`, `printf`, `basename`, `dirname`, and readers (`cat`, `head`, `wc`, `grep`, …) given files from the worktree. Redirections name a file in the worktree or the lane's scratch directory (or `/dev/null`), never under `.git/` or `.claude/`; one that writes into the worktree (`>`, `>>`, `>|`, `&>`, `<>`) follows the role's `writes` as Edit does, so a generated path, another role's path, or one no role owns is refused (scratch is where a lane writes freely). The readers a lane may run (`grep`, `head`, `tail`, `wc`, `ls`, `diff`, `cat`, and `cut`, `tr`, `sort`, `uniq`), as commands or inside `$(...)`, name files in those two places only, file-taking options included (`grep -f`, `diff --exclude-from`). Their long options are matched exactly from a table, and any other is refused: GNU tools take a unique prefix (`diff --from=F` is `--from-file=F`) and some an optional value (`--unified[=N]`), so a guess would read the wrong word as a file. No `tail -f`, `sort -o`, `sort -T`. A `cd` goes only to the worktree's top, since a command's paths are judged from where it starts. `make` never takes `--eval`, `-f`, `-C`, `-I` or a `SHELL`/`MAKEFLAGS` override, and `go` never `-exec`, `-toolexec` or `-overlay`: a lane runs the repo's own targets. `terraform` runs only `version`, `fmt` (directories in the worktree), `validate` and `init -backend=false`, each with a fixed option set, `-chdir` inside the worktree: never a backend, `plan`, `apply`, state, `-from-module` or `-plugin-dir` (init still fetches the providers the config names). A charter allows it with `lane_allow`; `init`/`validate` need `-chdir`, so they go through a make target.

- **Identity.** The coordinator pins the lane's role: `role set` and `role clear` refuse a pinned session, only `role require/show/owners/check-cmd/reviewed/list` run, `story --sid` (and its abbreviations) is refused, and the coordinator fails a lane whose role changed.
- **git.** No global options (`-c`, `-C`, `--git-dir`, `--work-tree`, `--exec-path`), no `--output`, `--upload-pack`, `--receive-pack`, `--exec`, `--ext-diff`, `--textconv` or `--no-index` (abbreviations included), and only status/diff/log/show/fetch/branch (read forms)/switch/checkout/add/commit/push/merge-base/rev-parse/rev-list/ls-files/ls-tree/grep/rebase/merge/worktree. `diff/log/show/ls-files` take paths from the worktree only; `grep` never takes `-O` (it runs a pager command) and reads `-f` pattern files from the worktree only. `rebase` and `merge` only catch the branch up: `rebase origin/main` while the branch isn't on origin (a rebased pushed branch would need a force push), `merge origin/main` once it is, and `--continue`/`--abort` (rebase also `--skip`); no `-i`, `-x`, `--onto`, strategies or other refs. Lanes run with `GIT_EDITOR=true`, so finishing either never waits on an editor. `fetch` is `origin` or `origin main`, no other refspec. `switch -c`/`checkout -b` (`-q` allowed) create a branch at HEAD, or at a start point that is HEAD's own commit (`origin/main` in a fresh worktree), named under the role's `branch_prefix` (never `origin/…`, `refs/…`, `main`); `switch <branch>` only to one of the role's own branches whose tree still carries `.claude/bin/role` and `.claude/roles`; nothing else moves the checkout, because a commit without `.claude/` has no hooks. `push` goes to `origin` with `-u/-q/-v` and an explicit plain branch name whose destination is under the prefix (no force, delete, tags, `--all`, `--mirror`, `main`, other roles' branches, wildcards, `$`), and never a change under `.github/workflows` or `.github/actions` against `refs/remotes/origin/main`: those run with the repo's secrets, so a person pushes them. When a lane's branch holds such a change and no PR is at its HEAD (none at all, an older merged or closed one, or an open one the lane couldn't push to), the coordinator brings it into your checkout as a local branch (the lane user writes a bundle of it beside its clone, readable by its group; you fetch only that branch from it, never from the clone, and never over a branch of yours that holds other commits), and the report gives the steps: `git push`, which updates the branch's open PR, or `git push` and `gh pr create --label role:<role>` when it has none. `coordinator handoff` does the same for a branch an earlier run left. `worktree add` and `remove` never touch `.claude/worktrees`, and `commit`/`add` read message and pathspec files from the worktree only.
- **gh.** No `--hostname`, and `-R/--repo` only as this repo's `github_repo`; PR verbs create/view/list/diff/checks/comment only (never merge, close, review, ready, edit); `run list/view` only (never rerun, cancel, delete, download, watch), with each verb's own option table (`-w` is `--workflow` on list, `--web` on view) and runs by number; PRs and issues by number (no URLs, `owner/repo#N`, branch names, or `--search`); `pr create --head` names a local branch, never `owner:branch`; `--body-file`, `--template`, `--recover` and `-F @file` only from the worktree or the scratch directory; `gh api` only inside this repo: reads of pulls, issues, commits, compare and branches, and POST replies to PR comments (`.../pulls/N/comments/ID/replies`, `.../issues/N/comments`), never a merge, approval, close, delete or GraphQL. `story --body-file` is held to the same two places.
- **Reads.** `Read`, `Glob`, `Grep` and `NotebookRead` stay inside the worktree the lane runs in (its `.claude/` skills included), its scratch directory, and Claude Code's output files for the session's own background commands (`<tmp>/claude-<uid>/<project>/<session>/tasks/<id>.output`) and its own large tool results (`<config>/projects/<project>/<session>/tool-results/<file>`), judged on the resolved path: never `/proc`, `~/.config`, the main checkout, or anything else on the machine. A command never expands a variable the lane's environment has (`$GH_TOKEN`, `$ANTHROPIC_API_KEY`), except a few that are paths or ids (`$HOME`, `$PWD`, `$CLAUDE_SESSION_ID`).
- **gh writes.** `pr create` takes title, body, body file, label, base, head, draft, fill and template only (no `--dry-run`, which may push, no reviewers or projects); `pr comment` and `issue comment` take a body or body file only (no `--edit-last`, `--delete-last`); `issue create` takes title, body and labels (no `--parent`, `--blocking`, projects or milestones); `issue close` a comment and a reason.
- **Scratch.** Each lane gets `<worktree>.scratch` beside its worktree (`COORDINATOR_SCRATCH`, named in its prompt), for PR and comment bodies, check output and notes: a body with backticks or `$` can't go inline, since Claude Code refuses a command substitution, and a file in the worktree is owned by no role, so writing it is an ask, which a lane is denied. Nothing there is committed; it goes when the worktree does. A lane edits files there without the ownership check (plain files only: never a `.git`/`.claude` path or a worktree there), and nowhere else outside its worktree. It's passed to the session with `--add-dir`.
- **Edits.** A lane edits only the worktree it runs in (and its scratch directory), judged on the resolved path (a symlink is where it points): not outside any repo, not another repo, not the main checkout, not another lane's or a throwaway worktree, and never `.claude/` or `.git/` of its own.

A deny-list for `gh pr merge/close/review/ready`, force pushes, pushes to main (as `main`, `HEAD:main` and `refs/heads/main`), `reset --hard` and `EnterWorktree`/`ExitWorktree` is also passed; it matches prefixes and is only a backstop.

**What this does not stop.** The boundary that holds against a lane that really tries is on the server, so set it up before running lanes unattended:

- **A lane's own code runs.** `make check`, the repo's build and test commands (and a charter's `lane_allow`) execute files the lane may have just written, with the user's credentials and outside any hook. CI does the same on a pushed branch: the guard stops workflow-file changes, not a changed `Makefile`, `scripts/`, test, or `package.json` script that a workflow then runs. So: protect `main` (the checks above enforce it), keep PR-triggered workflows free of secrets or put them behind an environment with required reviewers, and run lanes as their own account. The lane process still runs as your OS user: code a lane runs can read anything you can, your gh keyring token included. Running lanes as a separate OS user (or in a container) is the step after this one.
- `role reviewed` is allowed: the review gate is the lane marking its own HEAD, as in any session. Likewise a Codex thread is "handled" once the lane replies with the tag, whether it fixed, disputed or deferred: the report lists how many threads a lane answered, under *Needs attention*, for you to read.
- What a lane writes in `--body`, a commit message or a file is its own text, and nothing bounds it: the guards keep files and environment values outside the worktree out of a lane's reach, not out of what it types.
- A lane can push onto another lane's branch of the same role, attach labels to its PR other than `role:<role>`, and read other refs and this repo's PRs and issues. Commits are signed by, and attributed to, the user.
- `git worktree add` can create a checkout at any path outside `.claude/worktrees` (the `/pre-pr` reviewer needs one), but the lane can't edit there.
- A session lane that ends `nothing to do` with a clean tree is done even if it closed or commented on issues first; the next plan re-reads the queue.
- Lanes are started in their own process groups. A coordinator that is killed outright (SIGKILL) leaves them running with their credentials until they finish; `coordinator status` shows them, and a `make check` child that `setsid`s away can outlive `terminate`. An interrupt between pinning a role and starting its process can leave a pin under `claude-roles/.pinned/`; it's inert.
- What a lane prints is its own: result lines are flattened to one short line without table or code syntax before the report carries them, and the lane logs are lane text, not the coordinator's; read them as such.
- A handoff that belongs in another repo (a planner's `release-change` issue) is a person's: lanes write in this repo only.
- A change to the hooks or this list reaches lanes only when it is merged and vendored, never from a lane's own PR in the same run.

`/sprint-run` is the interactive entry: plan, show the owner the lanes and what's waiting, ask, run, report. `bin/story` retries a missing story a few times (`STORY_FIND_RETRIES`, `STORY_FIND_DELAY`) because the Project's item listing can trail a create by minutes, and `story create` reads the repo's issue titles so a lagging listing can't reuse an ID.

## Review gate

`/pre-pr` runs the repo's check command (`check:` in `targets/<t>/roles/_repo.md`, default `make check`), then a fresh-context reviewer subagent that works through [`skills/pre-pr/references/checklist.md`](skills/pre-pr/references/checklist.md) and mutation-tests new tests in its own worktree. P0/P1 findings are fixed test-first, for at most 4 rounds. When a round is clean it runs `role reviewed`, which records HEAD in `<git-common-dir>/claude-roles/.reviewed/<sha>`. Any new commit closes the gate again. `role reviewed` runs as its own Bash call: the hook judges a whole command before any of it runs, so `role reviewed && git push` is denied, and the denial says to split it.

The hook tokenizes the command quote-aware (shlex), after dropping comments and quoted heredoc bodies. An unquoted heredoc's body is scanned for `$(…)` and backticks, which the shell runs. It finds every `git push` and `gh pr create`/`gh pr new`, including:

- env prefixes and `GIT_DIR=`
- git global options (`-C`, `-c`, `--git-dir`, `--no-pager`); `--work-tree` and `GIT_WORK_TREE` don't change which repository is pushed
- git aliases, inline (`-c alias.x=push`) or from config
- `cd`/`pushd`/`popd`/`cd -`, scoped to subshells
- compound commands (`if …; then`, `{ …; }`, `!`)
- wrapper commands: `sudo`, `xargs`, `timeout`, `nice`, `env`, `command`, `watch`
- `bash -lc`, `eval`
- `$(…)` and backticks, both unquoted and inside double quotes

Redirections (`2>&1`, `>/dev/null`) are removed before refspecs are read. For a push, the hook resolves each refspec source and requires every one to be reviewed or already on `origin/main`. With no refspec, that's HEAD. A refspec that is a shell expansion (`"$(git branch --show-current)"`, `"$B"`) is checked as HEAD. Deletes (`--delete`, `:branch`) pass. `--all`, `--mirror`, and `--tags` are denied, so push by name instead. `gh pr create` checks `--head` (the local branch, else `origin/<branch>`; an owner prefix is dropped), and HEAD when there's no `--head`. A `--head` that resolves to neither is denied.

The checklist is built from bugs that previously reached Codex or architecture review. When review catches a new bug class, add it to the checklist.

**Codex wait.** After the push, `/pre-pr` runs `bin/codex-review wait <PR>`. It polls Codex's status comment (the one marked `codex-pull-request-review-summary`, which Codex updates when a review finishes) for a row on the pushed commit, up to 10 minutes. Codex doesn't review on push, so it posts `@codex review` once when no row appears within 2 minutes. When the review completes with findings, `/pr-comments <PR> --auto` fixes them test-first, re-runs `/pre-pr --no-codex` on the fixes, pushes, replies on every thread, and the wait repeats on the new HEAD, at most 3 rounds. `--auto` skips the wait for the owner's go-ahead on the triage table; disputes are posted only with reproducible evidence, and anything disputed, routed, or deferred is listed in the report. A `failed` or `timeout` state is reported as such, never as clean. Before it pushes to an existing branch, `/pre-pr` checks the PR isn't already merged.

Limits. The hook is a parser, not a shell. It doesn't see a push run from a script or make target (`./ship.sh`, `make release`), a shell function defined earlier, a gh alias, a path held in an unexpanded `$VAR`, or the refs that `push.default=matching` or `remote.*.push` would add to a bare `git push`; it checks HEAD for that. A determined agent could run `role reviewed` without reviewing. As with shell writes around the ownership hook, the skill and charter forbid it; the hook can't tell the difference.

## Git and shell guards

`hook-pre-bash` denies these in every mode: a role session, `/role none`, or no role set. It uses the same parser as the review gate, so it sees through wrappers, `bash -c`, aliases, and `-C`.

| Command | Denied when | Do instead |
|---|---|---|
| `git add -A` / `--all` / `.` / `:/`, `git commit -a` (`-am`) | always | Stage explicit paths and leave changes you didn't make unstaged |
| `git restore` (worktree), `git checkout -- <paths>` or `git checkout <path>`, `git checkout -f`, `git reset --hard`, `git switch -f` / `--discard-changes` | the paths they overwrite have uncommitted changes (`git status --porcelain`, tracked files); for a forced switch or `reset --hard <rev>`, also untracked files, directories, or symlinks at paths `<rev>` tracks (or at their parent paths) | Commit or `git stash`, or have the owner discard them themselves (`! git …`) |
| `pkill -f` / `--full` | always | Find the process with `pgrep -a`, then `kill <pid>` |
| `rm` / `unlink` / `mv`, `rm -r` on a directory holding one, `git clean` (judged by its own `-n` dry run), a truncating `>` / `>|` / `&>` | the target is an untracked or ignored local env file that exists: `.envrc`, `.env`, `.env.*`, `.netrc`, `.tool-versions`. A tracked `.env.example` is fine | Stop and ask the owner. Git can't restore these, and they often hold secrets |
| The check command (`role check-cmd`, `make check`, `make test`) piped into `tail`, `head`, `grep`, `rg`, `less`, `more`, or `tee` | `set -o pipefail` (or `setopt pipefail`, or `bash -o pipefail -c`) isn't in effect and the command doesn't read `PIPESTATUS`, so the exit status read is the reader's | `set -o pipefail; make check 2>&1 \| tail -50`, or redirect to a file and read it |

Abbreviated long options (`--har`, `--disc`, `--fu`) are expanded first, as git and pkill accept unique prefixes. `--force` wins over `-b`, `--pathspec-from-file` counts as the whole tree, and every non-ref word after `git checkout` is a pathspec, even if it doesn't exist on disk. `git restore --staged` (unstaging), `reset --soft`/`--mixed`, `checkout -b`, and a plain branch switch are allowed. Git refuses to switch branches over conflicting changes, so a plain switch can't lose work. Hooks run for subagents' tool calls too, so the guards cover them. Like the review gate, the guards are a parser: a script, a make target, `find -delete`, or `xargs rm` isn't seen, and a directory too large to walk (over 20,000 entries) is let through.

## PR comments

`/pr-comments [N]` works the open review threads on a PR carrying this session's `role:` label. It reads them over REST, since GraphQL is blocked in cloud sessions and thread resolution isn't visible. It verifies each claim (a failing test, a command, or file:line) and shows the owner a triage table. After they approve, each thread is handled one way:

| Disposition | Action |
|---|---|
| Fix | Test-first, explicit-path commits |
| Route | Another role's path or a contract question: `story comment --to <owner>`, or a plain issue |
| Dispute | With evidence |
| Defer | To an issue |
| Answer | A reply with a citation |

The fixes go through `/pre-pr` before the push. Then each inline thread gets a reply starting `**pr-comments:**`, and review bodies and conversation comments get a `**pr-comments:** re review|comment <id>:` line naming them. That's how a later run knows an item was handled. The skill never merges, approves, dismisses, or resolves threads. Fixed comments that the pre-PR reviewer should have caught become proposed lines for `skills/pre-pr/references/checklist.md`.

`role owners <path>…` prints the owner of each path: role names, `generated`, or `unowned`. Routing uses it.
