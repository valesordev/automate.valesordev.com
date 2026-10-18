---
role: sre
extends: sre
aliases: [ops, reliability]
branch_prefix: sre/
writes: [deploy/, .github/, Makefile, scripts/, docs/runbooks/, docs/specs/slo/, docs/glossary.md]
skills: [sre-start-sprint]
tools: [gcx]
transitions: [ready>in-progress@lane]
story_edits: [body]
on_merge: [in-progress>review@lane]
lane_allow: [Bash(terraform version:*), Bash(terraform fmt:*), Bash(make tf-validate:*)]
---
## Demo Game server

The sre role's specifics for the Demo Game server fixture.
