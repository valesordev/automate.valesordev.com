# CLAUDE.md — automate.valesordev.com

Agent configuration for Valesor Development software repos. Files here define **how agents behave** in those repos: the `valesor-dev` Claude Code plugin (skills, hooks, `bin/` tools, base role charters, vendored workflows), the per-repo targets that vendor it, and the toolkit that builds and installs both. When working in this repo you build and maintain agents; you don't do their downstream work.

## Mission

Help the owner move projects forward, build lasting skills while doing it, and use AI deliberately without letting it take over. Every agent and skill here is judged against all three.

## Sources of truth

- **This repo**: everything agents execute, and how to use it. `docs/` is authoritative for usage (install, roles, skills, contracts), with the reference beside the code (`plugins/valesor-dev/README.md`, `toolkit/README.md`). It is public: no secrets, no personal names (say "the owner"), no private Notion links, no machine-specific paths. `make check-public` enforces it (Notion links, the owner's denylist, gitleaks over the whole history), and CI runs it on every PR and push to `main`.
- **Direction** (rationale, roadmap, decision log) is the owner's, kept privately. Nothing here depends on it: a doc that needs a decision states the decision.
- **Docs site.** `make docs-export SITE=<sites monorepo>` writes `docs/` to its `content/automate.valesordev.com/`; the site build and deploy live there. Change a usage doc here in the same PR as the behavior it describes.
- Repos that pin this one (`deps.json`) own their own targets and plugins. A change here reaches them only when they move their pin.

## Repository layout

```
.
├── .claude-plugin/marketplace.json   # marketplace "valesordev"
├── CLAUDE.md                         # this file
├── docs/                             # usage docs: getting started, working in a role, role catalog (exported to the docs site)
├── Makefile                          # validate / check-public / tools / test / build / package / install / check-target / docs-export / mirror / cutover / dev
├── toolkit/                          # install, render, tool_perms, check_conventions, check_public, tools, package, scaffold, export_docs, update-agents (+ sources.py); its own fixture tests
├── plugins/valesor-dev/
│   ├── .claude-plugin/plugin.json
│   ├── README.md                     # roles + assist levels, hooks, coordinator, lanes: the long-form docs
│   ├── skills/<skill>/SKILL.md       # + references/
│   ├── bin/                          # role, story, sprint-state, project-mirror, coordinator, codex-review, ghapp
│   ├── hooks/hooks.json              # uses ${CLAUDE_PLUGIN_ROOT}
│   ├── roles/<name>.md               # base charters (product, sre, visual-designer) that target roles `extends`
│   ├── tools/<tool>.md               # tool definitions (gcx) + .commands.txt snapshot + generated .permissions.json
│   └── workflows/                    # GitHub Actions vendored to a target's .github/workflows/
├── targets/<repo>/                   # solo7-media: target.json, settings.json, main-ruleset.json, roles/
└── tests/                            # plugin and target tests; fixtures/consumer/ holds generic targets
```

The toolkit runs from `toolkit/` here, never pinned: this repo depends on nothing. Every script takes `--root DIR`. Plugins, `extends` bases, and `{{include}}`s resolve in the repo first, then in each `deps.json` dependency, so a consumer can vendor `valesor-dev` and extend its base roles from its own targets. A toolkit or plugin change that consumers need ships as a tag (`v<major>.<minor>.<patch>`); consumers `make pin DEP=valesordev REF=<tag>`.

### Delivery

| Surface | How agents arrive |
|---|---|
| Claude Code in a target repo (local **and cloud**) | `make install TARGET=<t> DEST=<repo>` vendors into the repo's `.claude/`, and you commit it there. |
| Claude Code outside a target repo | `make dev` registers this checkout as a marketplace; install `valesor-dev` from it. |

Cloud sessions don't load plugins, so vendoring is the rule for target repos. Don't also install a vendored plugin locally, or its skills and hooks run twice. `install.py` maps `skills/` → `.claude/skills/`, `bin/` → `.claude/bin/`, and `workflows/` → `.github/workflows/`, merges `hooks/hooks.json` and `targets/<t>/settings.json` permission entries into `.claude/settings.json`, and composes `targets/<t>/roles/` (resolving `extends`) → `.claude/roles/`. `.claude/.automate.json` records the source commits and file hashes; `make check-target` reports drift, and install refuses to overwrite a managed file edited in the target.

### Roles, lanes, and the coordinator

`plugins/valesor-dev/README.md` is the reference: `/role` sessions and worktrees, path ownership, PR labels, git and shell guards, the review gate, `/pr-comments`, the Project mirror and `stories: github`, sprints, story transitions, releases, base roles, and coordinator lanes (their OS user, GitHub App, allow-list, and hook copy). Change behavior there and in the code together.

### Targets

| Target | Repo | Roles |
|---|---|---|
| `solo7-media` | `valesordev/solo7.media`: the brand kits and sites monorepo, deployed to Cloudflare Workers | `product` (L3), `pm`, `architecture`, `visual-designer` (L2, a sprint lane), `implementation`, `sre`. Bootstrap: `targets/solo7-media/bootstrap.md`. |

Repos that pin this one keep their own targets there and vendor `valesor-dev` from the pin.

## Doctrine (applies to every agent)

**Assist levels.** Each role declares one on the first line of its body (`Assist level: L<n> — <why>`) and in the plugin `README.md` table.

| Level | Name | Agent produces | The owner produces |
|---|---|---|---|
| L0 | Coach | Questions, prompts, critique, practice plans | The work |
| L1 | Guide | Explanations, fragments, reviews, examples | The work |
| L2 | Pair | Drafts, scaffolding, verification | Design, key decisions, final call |
| L3 | Delegate | The deliverable | Review and approval |

Rules:

- Sessions end with one concrete next action, sized for a Kanban pull (no invented due dates).
- Irreversible or outward-facing actions (pushes to shared remotes, publishing, merges) require confirmation.
- No secrets in the repo. Tokens and credentials never; resource IDs only where they aren't private.

## Authoring conventions

### Skills (`plugins/<p>/skills/<name>/SKILL.md`)

- `name` = directory name (kebab-case). `description` states *when to trigger*, with concrete phrases and explicit non-triggers. It is the routing signal.
- Body: workflow steps, decision rules, output format. Long material goes in `references/`.
- One skill, one job. If the description needs "and also", split it.
- Every skill ships `references/examples.md` with at least one prompt showing the expected trigger and behavior.

### Tools

- A role names the CLIs it uses for outside systems in `tools:`, and `_repo.md` names the ones every role uses. Definitions live in `plugins/<p>/tools/` or `targets/<t>/tools/` (see the plugin README, Tools).
- Never hand-edit `<tool>.permissions.json`. Re-snapshot (`make tool-snapshot`), or change `read_verbs`/`always_ask` and `make build`.

### Roles

- Base roles (`plugins/<p>/roles/<name>.md`) hold the generic charter. A target role file with `extends: <name>` gets the base body followed by its own repo-specific body.
- A target enables a role only by adding `targets/<t>/roles/<role>.md`, with frontmatter (`role`, `extends`, `aliases`, `summary`, `branch_prefix`, `writes`, `skills`, `transitions`, `story_edits`, …) and a charter.

### Tests

Tests use the fixture targets in `tests/fixtures/consumer/` (`game-server`, `game-content`, and a link to `solo7-media`), installed with `--root tests/fixtures/consumer`. Never install a consumer repo's real target in a test here.

## Workflow

1. **Read** the plugin README and the existing files.
2. **Decide** assist level, boundaries, and naming with the owner if they aren't documented. These are the owner's calls.
3. **Draft.** For edits, show the diff.
4. **Validate:** `make validate test`.
5. **Build:** `make build` must leave the tree clean.
6. **Commit:** feature branch, signed commits, conventional-commit subject scoped by plugin (`feat(valesor-dev): …`, `feat(toolkit): …`). The owner merges with a merge commit, so a PR may carry several commits. Fixes for review comments go in new commits, not amends or force-pushes. Bump `version` in `plugin.json` on behavior changes, once per PR.
7. **Release:** after a merge that consumers need, tag it and say which pins to move.

## Makefile targets

| Target | Purpose |
|---|---|
| `make validate` | `check-render`, then `claude plugin validate` on the marketplace and each plugin, then `toolkit/check_conventions.py` (examples, skill upload limits, assist levels, README roles tables) |
| `make check-public [COMMITS=<range>]` | The public-readiness gate: `toolkit/check_public.py` with the denylist at `$AUTOMATE_DENYLIST` (a file outside every repo; CI reads the `AUTOMATE_DENYLIST` secret). Hits name the denylist line, never the term |
| `make tools` | Fetch the pinned gitleaks into `.tools/` |
| `make check-render` | Fail if any `plugins/*/src/**` render or tool permissions file is stale |
| `make test` | The toolkit's fixture tests, then `tests/` (role hooks, story, mirror, coordinator, targets) |
| `make build` | Render `plugins/*/src/**` in place, and each `tools/<tool>.permissions.json` from its snapshot |
| `make tool-snapshot TOOL=<tool>` | Snapshot a CLI's commands into `tools/<tool>.commands.txt` (needs the CLI installed) |
| `make package [ALL=1]` / `make package-done` | Zip skills changed since the last upload for claude.ai / record them as uploaded |
| `make install TARGET=<t> DEST=<repo>` | Vendor a target into `<repo>/.claude/` (commit it there) |
| `make check-target TARGET=<t> DEST=<repo>` | Report drift; exit 1 if the target is edited, outdated, or stale |
| `make docs-export SITE=<repo> [DRY=1]` | Export `docs/` to the sites monorepo's `content/automate.valesordev.com/` (commit it there) |
| `make mirror DEST=<repo> [TARGET=<t>] [APPLY=1]` | Run `project-mirror` against a clone (dry run without `APPLY=1`) |
| `make cutover DEST=<repo> [REF=stories-final] [APPLY=1]` | Hand a mirrored repo's board to GitHub (dry run without `APPLY=1`) |
| `make dev` | Register this checkout as local marketplace `valesordev` |

## Issues and backlog

- The backlog for these agents is this repo's GitHub issues, on the org's *Automation* Project. Labels: `plugin:<name>`, `type:skill|agent|tooling|contract`, `kind:bug|refinement|feature`, and `source:session` for anything found while using an agent. Milestones are plugin versions (`<plugin> <version>`). An issue gets one when it's committed to that release.
- A behavior to change uses the *Skill refinement* form (`.github/ISSUE_TEMPLATE/`): observed, sanitized excerpt, expected, proposed change, assist level.
- An agent that misbehaves during a session may draft a refinement issue for its repo. It creates the issue only on the owner's yes, as with `content-gap`.

## Definition of done

- Every role declares its assist level, triggers, behaviors, handoffs, and session close.
- `make validate test check-public` passes; `make build` is clean.
- `solo7-media` re-installed and `make check-target` clean when the change touches it. Design new `valesor-dev` machinery for every kind of target, not one repo.
- Consumers that need the change get a tag, and their pin moves in their own PR.
- Signed commits on a feature branch, `plugin.json` version bumped if behavior changed.

## Working style

The owner is a senior engineer. Be terse, structured, implementation-first. Implement rather than narrate plans. Ask only for decisions that are the owner's: scope, assist level, boundaries, and naming.
