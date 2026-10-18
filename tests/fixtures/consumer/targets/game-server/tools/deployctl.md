---
tool: deployctl
kind: cli
domain: Demo Game deploys
check: false
instead_of:
  commands: [rawdeploy]
  url: ['^https?://deploy\.example\.test/']
---
Use deployctl for deploys. (Fixture: its check always fails.)
