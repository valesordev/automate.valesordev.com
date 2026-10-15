---
role: implementation
summary: Server code to the story contract | tested
aliases: [impl, dev]
branch_prefix: impl/
writes: [server/, internal/, cmd/, admin/, content/, agents/, client/, testdata/, docs/glossary.md]
skills: [impl-start-sprint]
lane_allow: [Bash(go build:*), Bash(go vet:*), Bash(go test:*), Bash(make test:*), Bash(make fmt:*), Bash(make proto:*)]
transitions: [ready>in-progress@lane]
on_merge: [in-progress>review@lane]
---
# Role: IMPLEMENTATION

Assist level: L2 — fixture role for tests.

You are the implementation agent for the Demo Game server fixture.
