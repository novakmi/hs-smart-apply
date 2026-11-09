# Unreleased

These are the changes that will go out in the next release.

## Fixed

- Expand defined empty variables to empty strings instead of leaving literal
  placeholders in environment values and paths such as `venv${VENV_SUFFIX}`.

## Added

- Evaluate `locals:` and `env:` blocks in YAML order, allowing locals declared
  first to supply reusable values to environment declarations and commands.
- Top-level `env:` declarations are exported to every workspace automatically,
  with workspace overrides taking precedence and no YAML merges required.
- A functional `hs-smart-apply` CLI with print and dry-run modes.
- Hierarchical environment and local variable expansion, plus `commands:` bubbling to panes.
- PyYAML runtime dependency and tests for expansion and CLI preview behavior.

### Contributors

[@novakmi](https://github.com/novakmi) (Michal Novak) created hs-smart-apply.
