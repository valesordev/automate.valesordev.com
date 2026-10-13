# automate.valesordev.com

Claude Code agent configuration for Valesor Development: a plugin marketplace (`valesordev`), the per-repo targets that vendor it, and the toolkit that builds and installs both.

| Path | What |
|---|---|
| `docs/` | How to use it: [getting started](docs/getting-started.md), [working in a role](docs/working-in-a-role.md), and the [role catalog](docs/roles.md). |
| `plugins/valesor-dev/` | Role-based development machinery: `/role` sessions with path ownership, sprint skills, the pre-PR review gate, PR comment handling, the story CLI and GitHub Project mirror, the lane coordinator, and base role charters (`product`, `sre`, `visual-designer`). See its [README](plugins/valesor-dev/README.md). |
| `targets/<repo>/` | One repo's agent config: which plugins it vendors, its role files, permission entries, and `main` ruleset. |
| `toolkit/` | Install, render, convention-check, package, and scaffold scripts. Other agent-config repos pin it through `deps.json`. See its [README](toolkit/README.md). |
| `tests/` | Plugin and target tests, against fixture targets in `tests/fixtures/consumer/`. |

## Use

```sh
make test                                   # toolkit fixture tests, then plugin and target tests
make validate                               # needs the `claude` CLI
make check-public                           # nothing private: needs AUTOMATE_DENYLIST (a file outside the repo)
make install TARGET=solo7-media DEST=../solo7.media
make check-target TARGET=solo7-media DEST=../solo7.media
```

`make install` vendors a target into the repo's `.claude/` (and `.github/workflows/`), where it is committed: cloud sessions load what a repo commits, not plugins. To use `valesor-dev` outside a target repo, `make dev` registers this checkout as a local marketplace.

## Depending on this repo

Another agent-config repo pins a tag of this one in its `deps.json` and runs the toolkit from `.deps/valesordev/toolkit/`. Its targets can then vendor `valesor-dev` and extend its base roles. The toolkit README has the Makefile snippet.

## License and contributing

Apache-2.0 ([LICENSE](LICENSE)). Issues are welcome; see [CONTRIBUTING](CONTRIBUTING.md) before opening a PR.
