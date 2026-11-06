# Usage

After installing hs-smart-apply with pip, run the command with a layout:

```sh
hs-smart-apply layout.yaml
```

Install from the GitHub repository with:

```sh
python -m pip install git+https://github.com/novakmi/hs-smart-apply.git
```

From a local project checkout, use `python -m pip install .`; for development,
use `python -m pip install -e .`. Apply mode also requires `herdr-spreader` to
be installed and available on PATH.

`--print` writes the expanded layout to stdout. `--dry-run` does the same
without invoking `herdr-spreader apply`.
Use `--version` to print the installed version.

For debugging from the project root without installing the package, run:

```sh
python -m pip install PyYAML
PYTHONPATH=src python -m hs_smart_apply --print layout.yaml
```

Layout nodes may define `env:` and `locals:` mappings. Child nodes inherit those
values, and locals in each mapping are evaluated in order. `$NAME`, `${NAME}`,
and `${NAME:-default}` placeholders are expanded. `commands:` accepts a list of
strings; commands inherited from ancestor nodes are joined with `&&` on each
leaf pane.

Top-level `env:` declarations are also copied into each generated workspace's
`env:` mapping so they reach pane shells and the programs they start. Workspace
declarations override shared values, including when an override is an empty
string. No YAML anchors or merges are needed:

```yaml
env:
  APP_ROOT: "$HOME/projects/app"
  MODE: development
workspaces:
  - name: api
    root: "$APP_ROOT"
    env:
      MODE: staging
    tabs:
      - panes:
          - commands:
              - printenv MODE
```

The generated workspace environment contains `APP_ROOT` and `MODE: staging`.
Only variables declared in the layout are copied. Process environment variables
remain available for expansion, and `locals:` remain expansion-only values.
The top-level `env:` block is consumed because Spreader accepts environment
declarations on workspaces rather than at the top level.

Short complete layouts are available in the [`examples/`](../examples/)
directory:

- [`01-basic.yaml`](../examples/01-basic.yaml) — one workspace and editor pane.
- [`02-scoped-variables.yaml`](../examples/02-scoped-variables.yaml) — shell environment variables, `env:`, and `locals:` expansion.
- [`03-inherited-commands.yaml`](../examples/03-inherited-commands.yaml) — workspace and tab commands bubbled to panes.
- [`04-global-scope.yaml`](../examples/04-global-scope.yaml) — shared `env:` exported to workspaces, with `locals:` and command expansion.

For example, preview the second layout with values from your shell environment:

```sh
PROJECT_ROOT="$PWD" NODE_ENV=staging hs-smart-apply --print examples/02-scoped-variables.yaml
```
