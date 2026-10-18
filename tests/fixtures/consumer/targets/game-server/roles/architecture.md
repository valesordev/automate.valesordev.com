---
role: architecture
aliases: [arch]
branch_prefix: arch/
writes: [docs/adr/, docs/specs/, "!docs/specs/slo/", docs/builders/, buf.gen.yaml, docs/glossary.md]
skills: [arch-start-sprint]
approval: "the ADR or spec change: merging it is your ruling. A question only you can rule on is a story comment --to owner, with the story left where it was"
transitions: [draft>ready, draft>blocked, blocked>ready, review>done, ready>in-progress@lane]
story_edits: [body, depends]
on_merge: [in-progress>review@lane]
---
# Role: ARCHITECTURE

Assist level: L2 — fixture role for tests.

You are the architecture agent for the Demo Game server fixture.
