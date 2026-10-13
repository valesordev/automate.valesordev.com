# Releasing

How a change merged here reaches the repos that run the agents. Nothing updates on its own: every hop is a PR in the next repo, and a repo keeps what it has until you move it.

## The two kinds of downstream repo

A repo's **target** decides where its install runs from.

| Kind | Its target lives in | It gets a release by |
|---|---|---|
| **Direct** (e.g. `solo7.media`) | `targets/` here | `make install` from a checkout of this repo at the tag |
| **Through an agent-config repo** (e.g. `andara.valesordev.com`) | that agent-config repo, which pins this one in `deps.json` | the agent-config repo moves its pin, then installs from itself |

An **agent-config repo** (`automate.system9studios.com`, `automate.bashburn.com`) is a repo like this one: its own plugins and targets, the toolkit run from `.deps/valesordev/toolkit/`. It vendors `valesor-dev` and `extends` the base roles from the pinned checkout. See [From another agent-config repo](getting-started.md#from-another-agent-config-repo).

To see what a repo is on now:

- an installed repo: `.claude/.automate.json` → `"source": "<agent-config repo>@<commit>"`
- an agent-config repo: `deps.json` → `"ref"`

## 1. Tag here

After the PR merges, on `main`:

```
git pull
git tag -a v<major>.<minor>.<patch> -m "v<…>: <what it ships, one line>"
git push origin v<…>
```

Tags are this repo's version, numbered on their own: they don't follow `plugin.json`'s `version`. Patch for a fix, minor for new behavior or a contract change. Until `v1.0.0`, a minor may need consumers to change their targets: say so in the tag message. Tag only what a consumer needs. A docs-only merge needs no tag.

## 2. Direct repos

From this repo, at the tag:

```
git checkout v<…>
make check-target TARGET=<t> DEST=<repo>     # drift before: edited, outdated, stale
make install      TARGET=<t> DEST=<repo>
make check-target TARGET=<t> DEST=<repo>     # clean after
git checkout main
```

In `<repo>`, on a `chore/` branch: commit `.claude/` and `.github/workflows/`, open a PR, merge it.

`install` refuses to overwrite a managed file edited in the target repo. Move that change here first (a PR, then a new tag), or drop it in the target repo.

## 3. Agent-config repos

In the agent-config repo, on a `chore/` branch:

```
make pin DEP=valesordev REF=v<…>
make deps
make validate test
```

Commit `deps.json`, open a PR, merge it. If a change here broke one of its targets, fix the target in the same PR. Then, if its own consumers need it, tag the agent-config repo the same way as step 1.

## 4. Repos installed from an agent-config repo

The same as step 2, run from the agent-config repo at its merged pin (or its tag):

```
make install TARGET=<t> DEST=<repo>
make check-target TARGET=<t> DEST=<repo>
```

The install uses whatever `valesor-dev` sits in its `.deps/valesordev/`, so run `make deps` first. If `.deps/valesordev` is a symlink to a local checkout (for working on both at once), the install takes that checkout as it is, not the pinned tag: point it back before a release install.

## Order

```
automate.valesordev.com   tag v<…>
├── solo7.media                         install TARGET=solo7-media           (step 2)
├── automate.system9studios.com         pin valesordev → v<…>               (step 3)
│   ├── andara.valesordev.com           install TARGET=andaras-world         (step 4)
│   └── andara.solo7.media              install TARGET=andara-content        (step 4)
└── automate.bashburn.com               pin valesordev → v<…>  (and system9studios, if it moved)
```

A repo two hops away (`andara.*`) gets the change only after the hop in between has merged. `automate.bashburn.com` pins both this repo and `automate.system9studios.com`: move both together when the second one tagged a release that carries the first.

## Checklist

- [ ] PR merged here; `make validate test check-public` was green
- [ ] Tag pushed, and the PR's "Release changes (after merge)" says which pins move
- [ ] Each direct repo: install PR merged, `check-target` clean
- [ ] Each agent-config repo: pin PR merged; tagged if its consumers need it
- [ ] Each repo installed from it: install PR merged, `check-target` clean
