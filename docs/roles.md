# Role catalog

The roles `valesor-dev` supports. A repo runs a role only if its target has the role file, and each target says exactly which paths the role writes. The ownership below is the usual split.

| Role | `/role` name (aliases) | Branch | Usually owns | Assist level | Skills |
|---|---|---|---|---|---|
| Product | `product` (`po`) | `product/` | `docs/product/`: vision, releases (`R<n>`), feature briefs (`FEAT-NN`), release reviews | L3 Delegate | `/release-plan`, `/feature-brief`, `/release-review`, `/product-triage` |
| Project manager | `pm` | `pm/` | Sprints, the roadmap (every milestone names its release), epics, new stories at `draft` | L2 Pair | `/pm-start-sprint`, `/pm-close-sprint` |
| Architecture | `architecture` (`arch`) | `arch/` | ADRs, specs (not SLOs), contract review, the definition-of-done review that moves stories to `done` | L2 Pair | `/arch-start-sprint` |
| Site reliability | `sre` (`ops`) | `sre/` | Deploy config, CI, the `Makefile`, scripts, runbooks, SLOs, observability review | L2 Pair (ops L3) | `/sre-start-sprint` |
| Implementation | `implementation` (`impl`) | `impl/` | Application source and its tests | L2 Pair | `/impl-start-sprint` |
| Visual designer | `visual-designer` (`design`) | `design/` | Brand kits: visual audits, brand docs, tokens, art direction, art slots with provenance | L2 Pair | `/design-start-sprint`, `/brand-audit`, `/art-direction`, `/art-slot`, `/brand-qa` |

Every role also uses `/role`, `/pre-pr`, `/pr-comments`, and `/project-sync`.

**Architecture specifies; SRE builds, ships, operates, and observes; implementation builds what runs in the product.** Architecture rules on contracts and decisions. SRE owns everything around the product that keeps it running and visible. A story's `lane:` says which builder role builds it.

**Base roles.** `product`, `sre`, and `visual-designer` ship as generic charters in `plugins/valesor-dev/roles/`. A repo's role file `extends:` one and adds what's specific to that repo. `pm`, `architecture`, and `implementation` are written per repo.

**Looks are decided in brand docs, never in code.** Where `visual-designer` runs, architecture readies a visual story only when it cites the brand doc section it implements, and builders ask brand questions with `story comment --to visual-designer`.

The charters' full text, the frontmatter that drives the hooks, and each skill's workflow are in the [`valesor-dev` README](../plugins/valesor-dev/README.md) and under [`plugins/valesor-dev/`](../plugins/valesor-dev).
