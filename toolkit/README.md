# toolkit

Build, check, and install scripts for agent-config repos: repos of Claude Code plugins (`plugins/<p>/`), per-repo targets (`targets/<t>/`), shared fragments, and scaffolds. A repo pins the toolkit through `deps.json` and runs it from `.deps/`. The toolkit never runs from a copy inside the repo.

| Script | Does |
|---|---|
| `install.py [--check] <target> <dest>` | Vendors `targets/<target>` into `<dest>/.claude/`: plugin skills, `bin/`, workflows, hooks, role files (`extends` composed), permission entries, and `update-agents`. `--check` reports drift. |
| `update-agents <tag> [--no-pr]` | Vendored as `.claude/bin/update-agents`: updates the repo it runs in from a signed tag of its agent-config repo. See [update-agents](#update-agents). |
| `render.py --tree\|--check <plugin>` / `render.py <src> <dst>` | Inlines `{{include path}}` into `plugins/<p>/src/**` renders (`--tree` writes, `--check` fails on stale), or renders one file. |
| `check_conventions.py` | Authoring conventions: `references/examples.md` per skill, skill upload limits, `Assist level: L<n>` on agents and roles, plugin README roles tables. |
| `package.py [--all\|--done]` | Zips skills changed since the last `--done` for claude.ai upload. |
| `scaffold.py [--into] <name> <dest>` | New repo, or an existing one, from `scaffolds/<name>/`. |
| `check_public.py [--denylist FILE\|--no-denylist] [--commits RANGE] [--no-gitleaks]` | The public-readiness gate. Fails on a private Notion link or a denylist entry (`$AUTOMATE_DENYLIST`, outside the repo; whole words or `re:` patterns; hits name the entry's line, never the term) in tracked files, and with `--commits` in those commits' messages and file versions; then `gitleaks git --redact` over the whole history. |
| `tools.py` | Fetches the pinned gitleaks into `.tools/`, checksum-verified. |
| `export_docs.py [--dry-run] <site>` | Copies `docs/**` to `<site>/content/<repo name>/` for the docs site. Links outside `docs/` become GitHub URLs at the exported commit; a broken link fails. `.export.json` there records the commit and files, so a re-export removes dropped files and a directory without it is never written. |

Every script takes `--root DIR`, the repo it works on. The default is the git toplevel of the working directory.

## update-agents

Every install also writes `.claude/bin/update-agents` (from `toolkit/update-agents`), whatever the target's plugins: a plugin that ships the same path is refused. Once installed, a repo updates itself from a release instead of being installed into from a checkout. `make update-agents REF=<tag>` runs `.claude/bin/update-agents <tag>`, which works from a clean `main` that matches `origin/main`:

1. It reads `repo` and `target` from `.claude/.automate.json`. An install made before the record had `repo` needs `--repo <owner>/<name>` once.
2. It fetches the tag from that repo and refuses unless `git tag -v` verifies it. Lightweight and unsigned tags are refused, and so are tags signed by a key your git config doesn't trust (for SSH signatures, `gpg.ssh.allowedSignersFile`).
3. In the fetched copy it runs `make deps` (when there's a `deps.json`), then `make install` and `make check-target` into this repo.
4. It creates `chore/update-agents-<tag>`, commits `.claude/` and `.github/`, pushes, and opens a PR against `main`. `PR=0` (`--no-pr`) stops after the commit.

A repo already at the tag gets no commit. The install's usual refusals still apply: a managed file edited in the repo stops it before anything is committed. The repo's Makefile needs one line, since install doesn't write it:

```make
update-agents: ## Update the vendored agents from a signed tag: make update-agents REF=<tag> [PR=0]
	@.claude/bin/update-agents "$(REF)" $(if $(filter 0,$(PR)),--no-pr)
```

## Dependencies

A repo lists what it pins in `deps.json`:

```json
{
  "valesordev": {"repo": "valesordev/automate.valesordev.com", "ref": "v0.1.0"}
}
```

`repo` is `<owner>/<name>` on GitHub, or any git URL. `ref` is a tag or commit. The repo's `make deps` checks each dependency out into `.deps/<name>/`, which is gitignored. Lookups try the repo first, then each dependency in `deps.json` order:

- `install.py`: a target's `plugins` (and the base roles they ship, for `extends`).
- `render.py`: each `{{include path}}`.

So a repo can vendor a plugin another repo ships, render a fragment another repo owns, and shadow either with its own copy. `.claude/.automate.json` records `source` (`<repo>@<commit>`), `repo` (the source's `origin`: `<owner>/<name>` on GitHub, else its URL), `ref` (the tag at the source's HEAD, when the tree is clean), and, when there are dependencies, `sources`: each repo's commit. `repo` and `ref` are what [`update-agents`](#update-agents) reads.

To work on a dependency and a consumer together, make `.deps/<name>` a symlink to a local checkout; `make deps` leaves a symlink alone.

### Consumer Makefile

```make
TOOLKIT := .deps/valesordev/toolkit
TK      := python3 $(TOOLKIT)

deps: ## Check out each deps.json dependency into .deps/<name>/ at its ref (a symlink is left alone)
	@python3 -c 'import json; [print(n, d["repo"], d["ref"]) for n, d in json.load(open("deps.json")).items()]' | \
	while read -r name repo ref; do \
	  dir=.deps/$$name; \
	  if [ -L "$$dir" ]; then echo "  $$name: $$dir is a symlink, left alone"; continue; fi; \
	  case "$$repo" in *:*) url=$$repo ;; *) url=https://github.com/$$repo.git ;; esac; \
	  [ -d "$$dir/.git" ] || git clone -q "$$url" "$$dir"; \
	  test -z "$$(git -C $$dir status --porcelain)" || { echo "$$dir has local changes"; exit 1; }; \
	  git -C "$$dir" fetch -q --tags origin; \
	  git -C "$$dir" -c advice.detachedHead=false checkout -q --detach "$$ref^{commit}"; \
	  echo "  $$name @ $$ref ($$(git -C $$dir rev-parse --short HEAD))"; \
	done

pin: ## Move one dependency: make pin DEP=<name> REF=<tag or commit>
	@test -n "$(DEP)" -a -n "$(REF)" || { echo "usage: make pin DEP=<name> REF=<tag or commit>"; exit 1; }
	@python3 -c 'import json, sys; n, r = sys.argv[1:]; d = json.load(open("deps.json")); d[n]["ref"] = r; open("deps.json", "w").write(json.dumps(d, indent=2) + "\n")' $(DEP) $(REF)
	@$(MAKE) -s deps
```

Targets that run the toolkit call `$(TK)/install.py --root $(CURDIR) …` and the like. A public repo adds `tools` (`$(TK)/tools.py --root $(CURDIR)`, gitignore `.tools/`) and `check-public: tools` (`$(TK)/check_public.py --root $(CURDIR) …`), and CI runs `make check-public` with the `AUTOMATE_DENYLIST` secret written to a file. Have them depend on a check that `$(TOOLKIT)` exists, so a fresh clone gets "run `make deps`" instead of a missing-file error.

## Tests

`make test` from the repo root runs `toolkit/tests/` against fixture repos (`tests/fixtures/home`, with `tests/fixtures/upstream` pinned in its `.deps/`). It never touches a real plugin or target.
