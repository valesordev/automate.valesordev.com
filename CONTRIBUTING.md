# Contributing

This repo is the agent configuration for Valesor Development's own projects. It's public so the toolkit and `valesor-dev` can be reused, but it's maintained for those projects first.

- **Issues** are welcome: bugs, questions, and ideas. If an agent did the wrong thing, use the *Skill refinement* form.
- **Pull requests:** open an issue first so we can agree on the change. A PR without one may be closed.
- Contributions are under Apache-2.0, the repo's license.

## Making a change

1. Branch from `main`. Commits are signed and use conventional-commit subjects scoped by plugin or area (`feat(valesor-dev): …`, `fix(toolkit): …`).
2. Run `make validate test check-public`. `check-public` needs a denylist file; to skip it, use `make check-public NO_DENYLIST=1`, and CI runs the full check.
3. If a plugin's behaviour changed, bump `version` in its `.claude-plugin/plugin.json`, once per PR.
4. Never commit secrets, personal names, machine paths, or links to private Notion pages. CI's `check-public` job fails on them.
