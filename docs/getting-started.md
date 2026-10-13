# Getting started

A repo gets the agents through a **target**: a directory in this repo (or in a repo that pins it) that says which plugins the repo vendors and which roles it runs. `make install` copies them into the repo's `.claude/`, and the repo commits them.

## Why vendored, not installed as a plugin

Cloud sessions (claude.ai/code, `claude --cloud`) don't load plugins and don't see `~/.claude/`. They load what the repo commits: `CLAUDE.md`, `.claude/skills|agents|commands|roles`, and the hooks in `.claude/settings.json`. So the skills, the `bin/` tools, the hooks, and the composed role charters are copied into the repo. This repo stays the source of truth: `.claude/.automate.json` records the source commit and a hash of every file it wrote.

Don't also install `valesor-dev` as a plugin in a repo that vendors it, or its skills and hooks run twice. Outside a target repo, `make dev` registers this checkout as the `valesordev` marketplace, and you install the plugin from it.

## 1. Add a target

```
targets/<repo>/
  target.json        {"description": "...", "plugins": ["valesor-dev"], "roles": "roles"}
  settings.json      optional: permissions.allow / ask / deny merged into the repo's settings
  main-ruleset.json  optional: the branch ruleset the coordinator expects on main
  roles/
    _repo.md         repo-wide: generated paths, the check command, the GitHub Project
    <role>.md        one file per role the repo runs
```

A role file is frontmatter plus a charter. `extends: <base>` composes a base role from the plugin (`product`, `sre`, `visual-designer`) ahead of the repo's own text. The frontmatter keys (`role`, `aliases`, `branch_prefix`, `writes`, `skills`, `transitions`, `story_edits`, `on_merge`) are described in the [`valesor-dev` README](../plugins/valesor-dev/README.md#roles). A repo enables a role only by having its file.

`_repo.md` names the `check:` command `/pre-pr` runs (default `make check`), the `generated:` paths no role edits, and, for repos on a GitHub Project, `project:`, `story_prefix:`, `stories: github` and `sprints: project`.

Then check it:

```sh
make validate test
```

## 2. Install into the repo

On a branch in the repo (commits must be signed if `main` requires it):

```sh
make install TARGET=<repo> DEST=../<repo>
cd ../<repo> && git add .claude .github && git commit -S -m "chore: install valesor-dev"
```

`install` also writes the vendored GitHub workflows under `.github/workflows/` (it refuses to replace one it doesn't manage) and merges the hooks and permission entries into `.claude/settings.json`, owning only the entries it added.

Create the labels the roles use once per repo: `role:<role>` for each role, plus `product` and `release-change` if the repo runs `product`.

## 3. First session

Start Claude Code in the repo, locally or in the cloud, and make `/role` the first message:

```
/role architecture
/arch-start-sprint
```

Until a role is set, the hooks deny edits inside the repo. See [Working in a role](working-in-a-role.md).

## 4. Updating

1. Change the source here (`plugins/valesor-dev/` or `targets/<repo>/`), then `make validate test`.
2. In the repo, on a `chore/` branch: `make install TARGET=<repo> DEST=<path>`, commit `.claude/` and `.github/`, open a PR.

`make check-target TARGET=<repo> DEST=<path>` reports drift at any time and exits 1 if the repo is edited, outdated, or stale. `install` refuses to overwrite a managed file that was edited in the repo: move that change here first.

## From another agent-config repo

A repo with its own plugins and targets can pin this one (a tag) in its `deps.json` and run the toolkit from `.deps/valesordev/toolkit/`. Its targets can then vendor `valesor-dev` and `extends` its base roles; the [toolkit README](../toolkit/README.md#dependencies) has the Makefile snippet. A change here reaches it only when it moves its pin (`make pin DEP=valesordev REF=<tag>`).
