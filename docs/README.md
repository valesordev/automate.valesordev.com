# automate.valesordev.com docs

How to use the Valesor Development agents: the `valesor-dev` Claude Code plugin, the per-repo targets that vendor it, and the toolkit that installs both. These pages are the usage reference. Direction (why the agents work this way, what's planned) lives with the owner and isn't needed to use them.

| Page | For |
|---|---|
| [Getting started](getting-started.md) | Giving a repo the agents: a target, the install, the first session, updates |
| [Working in a role](working-in-a-role.md) | Day to day: one session, one role; what the hooks allow; the sprint cycle; PRs |
| [Role catalog](roles.md) | Every role: what it owns, its assist level, its skills |

Reference, kept beside the code it describes:

- [`valesor-dev` README](../plugins/valesor-dev/README.md): roles, hooks, the Project board, stories, the coordinator and lanes, the review gate, guards, PR comments.
- [Toolkit README](../toolkit/README.md): install, render, conventions, package, scaffold, docs export, and pinning this repo from another one.

## Assist levels

Every role declares one, and it bounds what the agent produces:

| Level | Name | Agent produces | The owner produces |
|---|---|---|---|
| L0 | Coach | Questions, prompts, critique, practice plans | The work |
| L1 | Guide | Explanations, fragments, reviews, examples | The work |
| L2 | Pair | Drafts, scaffolding, verification | Design, key decisions, final call |
| L3 | Delegate | The deliverable | Review and approval |
