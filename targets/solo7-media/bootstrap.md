# Bootstrapping valesordev/solo7.media as the sites monorepo

Decided by the owner on 2026-10-07:
- Reuse `valesordev/solo7.media` in place.
- Rebuild the sites from the Notion design targets, salvaging the pieces worth keeping.
- Deploy to Cloudflare Workers static assets.
- Run the board on org Project #3, reset first.
- Valesor Development goes first.

Steps marked **the owner** are outward-facing; the agent prepares them and they run or confirm them.

## 1. Restructure the repo (one PR, `/role none`)
The live solo7.media site keeps deploying throughout.

1. Branch `chore/monorepo-skeleton` from `main`.
2. `git mv` the current site into `sites/solo7.media/`. Point
   `.github/workflows/deploy.yml` at it (`working-directory`, artifact path).
   It still deploys to GitHub Pages until its replacement ships.
3. Add the skeleton:
   ```
   package.json            # npm workspaces: sites/*, packages/*, brands/*
   Makefile                # check, help, and `-include brands/*/brand.mk`; sre fills in the rest in R1
   brands/<slug>/README.md # valesordev, system9studios, solo7productions, solo7media, bashburn
   sites/                  # solo7.media/ (legacy) only, for now
   packages/
   docs/{product,adr,specs,sprints,runbooks}/
   ```
4. Write `CLAUDE.md` (template below).
5. `make install TARGET=solo7-media DEST=<clone>` from automate, and commit
   `.claude/` and `.github/workflows/story-merge.yml`.
6. Create the labels once (**the owner**):
   `gh label create role:{product,pm,architecture,visual-designer,implementation,sre}`,
   `product`, `release-change`, `sprint`, `sprint:planned`, `sprint:active`.
7. Verify the Pages deploy still serves solo7.media after merge.

## 2. Reset Project #3 (**the owner**: board writes)
- Close the 4 old brand-kit items with a comment saying the brand kit restarted.
- Delete the `Iteration` field, and reset the options of the existing `Lane`
  field to architecture, visual-designer, implementation, sre. Add these:
  - `Sprint` and `Rank` (number);
  - `Plan` (Current / Next / Later);
  - `Component` (valesordev, system9studios, solo7productions, solo7media, bashburn, infra, core);
  - `Size` and `Risk`;
  - `Story ID` (text).
  REST can't add single-select options, so create them in the UI or with `gh project field-create`.
- Rename the board only if `project:` in `targets/solo7-media/roles/_repo.md`
  changes with it.
- Link the repo to the Project.
- Check that `.claude/bin/story list` works from a local session and from a cloud session.

## 3. Plan
1. `/role product` → `/release-plan`: the vision PR first, then `releases.md` once
   it merges. Expected first cut:
   - **R1:** the pipeline plus Valesor, end to end.
   - **R2:** System 9.
   - **R3:** Solo7 Productions.
   - **R4:** bashburn.com.
   - **R5:** solo7.media.
2. `/role pm` → `/pm-start-sprint`: the roadmap, R1's epics and stories, SPRINT-01.

## 4. Retire the old repos (after each site's Cloudflare cutover)
Archive these on GitHub (**the owner**):
- `valesordev.com`, `system9studios.com`, `solo7productions.com`, `bashburn.com`;
- `brand-kit`, `solo7-theme`, `ui`, `astro-site-template`.

## CLAUDE.md section numbers
The valesor-dev sprint skills cite the repo charter by section number, so
keep these numbers:

| § | Section |
|---|---|
| 1 | What this repo is: the brands, the sites, the imprint roles (Solo7 imprint definitions) |
| 2 | Ownership: points to `.claude/roles/`; one role per session |
| 3 | Layout: `brands/<slug>/` skeleton, `sites/<domain>/`, `packages/`, `docs/` |
| 4 | Conventions and the story contract format (Why, Acceptance criteria, Brand citation, Interface contract, Out of scope, Observability requirements, Test plan) |
| 5 | Structure rules: static output, JS-off readable, permanent URLs, tokens only, no cross-brand components |
| 6 | Grooming: questions for the owner batched, at most 3 per sprint checkpoint |
| 7 | Observability: Faro rules, SLO before alert |
| 8 | Definition of done: AC met, `make check` green, `/brand-qa` pass for visual changes, preview verified, instrumentation verified by sre where relevant |
| 9 | Automation contract: every step a make target |
| 10 | Voice and refusals common to every site: no funnels, newsletters, social feeds, or comments |
| 11 | Session start: `git fetch`, read this file, `_repo.md`, your role, then the brand docs your story cites |
