# Unreleased

These are the changes that will go out in the next release.

## Fixed

- Expand defined empty variables to empty strings instead of leaving literal
  placeholders in environment values and paths such as `venv${VENV_SUFFIX}`.

## Added

- Evaluate `locals:` and `env:` blocks in YAML order, allowing locals declared
  first to supply reusable values to environment declarations and commands.
- Optional `container:` declarations on layout nodes that run pane commands
  inside `lxc`, `docker`, or `podman` containers and leave an interactive shell
  running in the container afterwards.
- An `05-container.yaml` example for the container feature, plus `06-lxc.yaml`
  and `07-podman.yaml` for the other container types.
- Top-level `env:` declarations are exported to every workspace automatically,
  with workspace overrides taking precedence and no YAML merges required.
- A functional `hs-smart-apply` CLI with print and dry-run modes.
- Hierarchical environment and local variable expansion, plus `commands:` bubbling to panes.
- PyYAML runtime dependency and tests for expansion and CLI preview behavior.

## Changed

- Only variables declared in the layout (`env:` and `locals:`) are expanded;
  `$NAME` references that the layout does not declare keep their literal text
  so runtime shell variables such as `$HOME` remain effective. `${NAME:-default}`
  fallbacks are unaffected.
- Within one node, `env:` and `locals:` declarations may reference each other
  and keys declared later in the same blocks; both are resolved against the
  node's complete declared scope, so declaration order no longer matters.

### Contributors

[@novakmi](https://github.com/novakmi) (Michal Novak) created hs-smart-apply.
