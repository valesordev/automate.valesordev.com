# toolkit

Build, check, and install scripts for agent-config repos: repos of Claude Code plugins (`plugins/<p>/`), per-repo targets (`targets/<t>/`), shared fragments, and scaffolds. A repo pins the toolkit through `deps.json` and runs it from `.deps/`. The toolkit never runs from a copy inside the repo.

| Script | Does |
|---|---|
| `install.py [--check] <target> <dest>` | Vendors `targets/<target>` into `<dest>/.claude/`: plugin skills, `bin/`, workflows, hooks, role files (`extends` composed), permission entries. `--check` reports drift. |
| `render.py --tree\|--check <plugin>` / `render.py <src> <dst>` | Inlines `{{include path}}` into `plugins/<p>/src/**` renders (`--tree` writes, `--check` fails on stale), or renders one file. |
| `check_conventions.py` | Authoring conventions: `references/examples.md` per skill, skill upload limits, `Assist level: L<n>` on agents and roles, plugin README roles tables. |
| `package.py [--all\|--done]` | Zips skills changed since the last `--done` for claude.ai upload. |
| `scaffold.py [--into] <name> <dest>` | New repo, or an existing one, from `scaffolds/<name>/`. |

Every script takes `--root DIR`, the repo it works on. The default is the git toplevel of the working directory.

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

So a repo can vendor a plugin another repo ships, render a fragment another repo owns, and shadow either with its own copy. `.claude/.automate.json` records `source` (`<repo>@<commit>`) and, when there are dependencies, `sources`: each repo's commit.

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

Targets that run the toolkit call `$(TK)/install.py --root $(CURDIR) …` and the like. Have them depend on a check that `$(TOOLKIT)` exists, so a fresh clone gets "run `make deps`" instead of a missing-file error.

## Tests

`make test` from the repo root runs `toolkit/tests/` against fixture repos (`tests/fixtures/home`, with `tests/fixtures/upstream` pinned in its `.deps/`). It never touches a real plugin or target.
