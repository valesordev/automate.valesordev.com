# Working in a role

## One session, one role

Start Claude Code in the repo and run `/role <name>` first. It records the role for the session and loads the charter: the base role (if the file `extends` one), then the repo's own text. `/role` alone shows the current role; `/role none` is an unrestricted session. Switching roles means a new session (`/clear`).

- **Local:** `/role <name>` moves the session into its own worktree (`.claude/worktrees/role-<role>-<id>`, branched from `origin/main`), so the checkout you launched from stays yours. Run several roles in parallel from one clone.
- **Cloud:** start a session on the repo and send `/role <name>` as the first message. There's no worktree; the session is the clone. Needs `git` and `python3` (both are in the cloud image).
- **After compaction or resume**, a `SessionStart` hook re-injects the charter, so the role holds.

If `main` requires signed commits, check once that a PR opened from a cloud session shows its commits as Verified.

## What the hooks allow

A `PreToolUse` hook on Claude's file tools enforces the role's `writes`:

| Path | Result |
|---|---|
| The role's own path | Normal flow |
| Another role's path | Denied |
| A path no role owns (`CLAUDE.md`, `.claude/`, …) | Asks the owner |
| A `generated` path | Denied in every role |
| Any path, with no role set | Denied inside the repo |

A hook on `Bash`:

- denies `gh pr create` without the role's `role:<role>` label, or with another role's;
- denies `git add -A` / `git add .`, `git commit -a`, and discarding uncommitted changes (`git restore`, `checkout -f`, `reset --hard`, …), in every mode;
- denies `git push` and `gh pr create` until `/pre-pr` has reviewed the commits being pushed;
- denies raw `gh issue` / `gh project` writes to stories: roles change stories with `.claude/bin/story`, within their `transitions:`.

The hooks guard Claude's tools, not every shell write. Every charter forbids routing around a denial.

## The sprint cycle

For a repo whose stories live on a GitHub Project (`stories: github`, `sprints: project`):

1. **pm** plans and activates the sprint (`/pm-start-sprint`).
2. **sre** reviews each story's observability, then **architecture** reviews its contract (`/sre-start-sprint`, `/arch-start-sprint`).
3. Builders (`implementation`, `sre`, `visual-designer`) pull their lane's stories and build them; a merge moves a story to `review`.
4. **architecture** checks each story against the definition of done and moves it to `done`.
5. **pm** closes the sprint (`/pm-close-sprint`).

**product** sits above the sprints: it owns the vision, the releases each milestone names, and feature briefs. Questions between roles are story comments (`story comment <ID> --to <role>`); a question only the owner can answer goes `--to owner`.

`/sprint-run` (the coordinator) can run the builders' lanes unattended, each in its own worktree with a narrow allow-list. Its setup and limits are in the [`valesor-dev` README](../plugins/valesor-dev/README.md#coordinator-bincoordinator-sprint-run).

## Before every PR

1. `/pre-pr`: runs the repo's check command, then an adversarial reviewer against a checklist built from past review findings. It records the reviewed commits; any new commit closes the gate again.
2. Push and open the PR with the role's label and `Story: <ID>` in the body.
3. `/pr-comments [N]` works review threads: it verifies each claim, shows you a triage table, fixes test-first or disputes with evidence, and replies on every thread. It never merges.
