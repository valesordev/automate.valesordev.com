---
tool: fakecli
kind: cli
domain: Fake Cloud
check: fakecli --version
catalog: fakecli commands
read_verbs: [list, get-*]
always_ask: [fakecli raw]
instead_of:
  commands: [rawcli]
  bash:
    - '\bcurl\b.*\.fake\.test'
credentials: fakecli's own config
---
Use fakecli for Fake Cloud.
