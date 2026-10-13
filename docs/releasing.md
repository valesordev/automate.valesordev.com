# Releasing

How a change merged here reaches the repos that run the agents. Nothing updates on its own: every hop is a PR in the next repo, and a repo keeps what it has until you move it.

## The two kinds of downstream repo

| Kind | Examples | It gets a release by |
|---|---|---|
| **Agent-config repo**: its own plugins and targets, this repo pinned in `deps.json` | `automate.system9studios.com`, `automate.bashburn.com` | moving its pin (`make pin`), then tagging itself |
| **A repo that runs the agents**: `.claude/` vendored from one agent-config repo's target | `solo7.media` (target here), `andara.valesordev.com` and `andara.solo7.media` (targets in `automate.system9studios.com`) | `make update-agents REF=<tag>` in the repo, at a tag of the agent-config repo its target lives in |

An agent-config repo runs the toolkit from `.deps/valesordev/toolkit/` and vendors `valesor-dev` and `extends` the base roles from that pinned checkout. See [From another agent-config repo](getting-started.md#from-another-agent-config-repo).

To see what a repo is on now:

- a repo that runs the agents: `.claude/.automate.json` → `repo` and `ref` (and `source`: `<repo name>@<commit>`)
- an agent-config repo: `deps.json` → `ref`

## 1. Tag

After the PR merges, in the agent-config repo, on `main`:

```
git pull
git tag -s v<major>.<minor>.<patch> -m "v<…>: <what it ships, one line>"
git push origin v<…>
```

The tag must be signed: `update-agents` refuses one that `git tag -v` doesn't verify. With `tag.gpgSign = true` in your git config, `git tag -a` signs too.

Tags are the repo's version, numbered on their own: they don't follow `plugin.json`'s `version`. Patch for a fix, minor for new behavior or a contract change. Until `v1.0.0`, a minor may need consumers to change their targets: say so in the tag message. Tag only what a consumer needs. A docs-only merge needs no tag.

## 2. Repos that run the agents

In the repo, on a clean `main`:

```
make update-agents REF=v<…>
```

It reads the agent-config repo and target from `.claude/.automate.json`, fetches the tag, verifies its signature, runs that repo's `make deps`, `make install` and `make check-target` into this one, then commits on `chore/update-agents-<tag>`, pushes, and opens the PR. Review and merge it. `PR=0` stops after the commit, for a look before pushing.

It refuses a dirty tree, a `main` that differs from `origin/main`, an unsigned or untrusted tag, and an existing branch of the same name. Already at the tag, it commits nothing. `install` refuses to overwrite a managed file edited in the repo: move that change into the agent-config repo first (a PR, then a new tag), or drop it in the repo. A refused install commits nothing; `git checkout -- .claude .github` puts the tree back.

The repo's Makefile needs one line, which install doesn't write; see the [plugin README](../plugins/valesor-dev/README.md#install). An install recorded before `repo` was (before `valesor-dev` 0.26.0) needs `--repo` once: `.claude/bin/update-agents v<…> --repo <owner>/<name>`.

## 3. Agent-config repos

In the agent-config repo, on a `chore/` branch:

```
make pin DEP=valesordev REF=v<…>
make deps
make validate test
```

Commit `deps.json`, open a PR, merge it. If a change here broke one of its targets, fix the target in the same PR. Then tag it (step 1) if the repos installed from it need the change, and run step 2 in each of them.

## Order

```
automate.valesordev.com                  tag v<…>                                  (1)
├── solo7.media                          make update-agents REF=<that tag>          (2)
├── automate.system9studios.com          pin valesordev → v<…>, then tag v<…>       (3, 1)
│   ├── andara.valesordev.com            make update-agents REF=<its tag>           (2)
│   └── andara.solo7.media               make update-agents REF=<its tag>           (2)
└── automate.bashburn.com                pin valesordev → v<…>  (and system9studios, if it moved)
```

A repo two hops away (`andara.*`) gets the change only after the hop in between has merged and tagged. `automate.bashburn.com` pins both this repo and `automate.system9studios.com`: move both together when the second one tagged a release that carries the first.

## Without update-agents

For a repo that doesn't have it yet, or to try a change you haven't tagged, install from a checkout of the agent-config repo:

```
make check-target TARGET=<t> DEST=<repo>     # drift before: edited, outdated, stale
make install      TARGET=<t> DEST=<repo>
make check-target TARGET=<t> DEST=<repo>     # clean after
```

Then, in `<repo>` on a `chore/` branch, commit `.claude/` and `.github/workflows/` and open a PR. In an agent-config repo, run `make deps` first: the install uses whatever `valesor-dev` sits in `.deps/valesordev/`. If that's a symlink to a local checkout (for working on both at once), the install takes the checkout as it is, not the pinned tag: point it back before a release install.

## Checklist

- [ ] PR merged; `make validate test check-public` was green
- [ ] Signed tag pushed, and the PR's "Release changes (after merge)" says which pins move
- [ ] Each agent-config repo: pin PR merged, tagged if its repos need it
- [ ] Each repo that runs the agents: `update-agents` PR merged
