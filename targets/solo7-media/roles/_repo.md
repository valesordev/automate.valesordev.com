---
generated: ["brands/*/dist/*", "sites/*/dist/*", "sites/*/.astro/*", package-lock.json]
check: make check
project: Redesign and Centralizing Imprint Sites
story_prefix: S7M
github_repo: valesordev/solo7.media
stories: github
sprints: project
story_components: [VAL, S9S, S7P, MED, BSH, INF, COR]
---
Repo-wide role config for the solo7.media monorepo. It holds the brand kits
and the sites for the four imprints and the blog. `generated` paths are never
edited by hand in any role: `make` builds `brands/<slug>/dist/` from tokens and
asset sources, and each site's `dist/` and `.astro/`; `npm install` writes
`package-lock.json`. Paths not in a role's `writes` need the owner's approval
(`CLAUDE.md`, `README.md`, `.gitignore`, `.claude/`).

| Brand (`brands/<slug>/`) | Site (`sites/<domain>/`) | Design target |
|---|---|---|
| `valesordev` | `valesordev.com` | Notion › Valesor Development › Brand Kit Project Direction |
| `system9studios` | `system9studios.com` | Notion › System 9 Studios › Brand Kit Project Direction |
| `solo7productions` | `solo7productions.com` | Notion › Solo7 Productions › Brand Kit Project Direction |
| `solo7media` | `solo7.media` | not designed yet; the legacy site keeps deploying from `sites/solo7.media/` until it is |
| `bashburn` | `bashburn.com` | Notion › bashburn.com Blog › Website Redesign Project Plan |

Each Notion page is the brief for its brand. Its homepage image is frozen into
`brands/<slug>/references/target-homepage.png`, and the approved
`references/visual-audit.md` decides which parts of that image are identity.

Roles: `product` owns what each site is for and what each release ships.
`pm` runs sprints on the board. `architecture` decides structure and reviews
contracts. `visual-designer` owns every brand decision the owner approves.
`implementation` builds components and sites. `sre` owns the build, CI,
Cloudflare, DNS, and Faro. Run one role per session (`/clear` before
switching).

## Branding is decided in brand docs, never in code
A story that changes how anything looks (a component in
`brands/<slug>/components/`, a page, an asset's use) cites the brand doc
section it implements, `brands/<slug>/brand/<file>.md#<section>`, in its
contract. Architecture doesn't move it to `ready` without that citation. When
a build hits something the brand docs don't decide, the builder asks with
`story comment <ID> --to visual-designer` and moves on. The answer is a brand
doc change the owner approves, never a value chosen in a component.

## Stories and sprints
Stories (`S7M-<comp>-NNN`), epics (`EPIC-NN`), and sprints
(`SPRINT-NN — <goal>`) are issues in this repo on the org Project named
above. GitHub is their source of truth from the start: there is no
`docs/stories/`, and nothing mirrors into the board. Change them only with
`.claude/bin/story`; the protocol is
`.claude/skills/role/references/stories-on-github.md`.

| Code | Component | Work |
|---|---|---|
| `VAL` | `valesordev` | Valesor brand and valesordev.com |
| `S9S` | `system9studios` | System 9 brand and site |
| `S7P` | `solo7productions` | Solo7 Productions brand and site |
| `MED` | `solo7media` | solo7.media brand and site |
| `BSH` | `bashburn` | bashburn.com brand and site |
| `INF` | `infra` | CI, the Cloudflare pipeline, DNS, Faro |
| `COR` | `core` | Workspaces, `packages/`, shared tooling |

- Lanes: `architecture`, `visual-designer`, `implementation`, `sre`.
- Status: `draft → ready → in-progress → review → done` (or `blocked`). PM
  creates at `draft`; architecture readies and closes; a lane starts its own
  stories; the story-merge Action moves them to `review` at merge.
- A sprint's plan is its issue plus the board (Sprint, Plan, Rank). Its record
  is `docs/sprints/SPRINT-NN-closeout.md` and `SPRINT-NN-demo.md`.
