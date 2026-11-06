# Unreleased

These are the changes that will go out in the next release.

## Added

- Top-level `env:` declarations are exported to every workspace automatically,
  with workspace overrides taking precedence and no YAML merges required.
- A functional `hs-smart-apply` CLI with print and dry-run modes.
- Hierarchical environment and local variable expansion, plus `commands:` bubbling to panes.
- PyYAML runtime dependency and tests for expansion and CLI preview behavior.

### Contributors

[@novakmi](https://github.com/novakmi) (Michal Novak) created hs-smart-apply.
