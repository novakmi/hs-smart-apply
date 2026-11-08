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

Layout nodes may define `env:` and `locals:` mappings, and child nodes inherit
those values. `$NAME`, `${NAME}`, and `${NAME:-default}` placeholders are
expanded, but only against variables declared in the layout — references that
no declaration defines stay literal so shell variables such as `$HOME` keep
working at runtime. Within one node, `env:` and `locals:` declarations may
reference each other, and later keys of the same blocks; both are resolved
against the node's complete declared scope (its own plus inherited values),
regardless of declaration order. References that no declaration defines remain
literal, and self-referential cycles are not resolved. `locals:` values are
expansion-only and never exported. `commands:` accepts a list of strings;
commands inherited from ancestor nodes are joined with `&&` on each leaf pane.
Quote entries that contain `"label: value"`-shaped text (`'echo "tag: v2"'`),
otherwise YAML parses them as mappings and they are ignored.

`locals:` and `env:` blocks are evaluated in the order they appear in YAML,
as are entries within each block. Put `locals:` before `env:` to reuse local
values in environment declarations and commands. Put `env:` first when locals
need values declared by that environment block. Parent scope values are
available to both blocks. Declare dependencies before the values that use them.

```yaml
locals:
  project_path: projects/app
env:
  APP_ROOT: "${HOME}/${project_path}"
workspaces:
  - name: api
    root: "$APP_ROOT"
    tabs:
      - panes:
          - commands:
              - echo "Project path is ${project_path}"
```

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
Only variables declared in the layout are copied and expanded. A `$NAME`
reference the layout does not declare keeps its literal text, so shell
variables such as `$HOME` keep working at runtime, and `locals:` remain
expansion-only values. The top-level `env:` block is consumed because Spreader
accepts environment declarations on workspaces rather than at the top level.

Layout nodes may also declare `container:` to run their commands inside a
container. Allowed types are `lxc`, `docker`, and `podman`; the declaration
inherits to child panes like an `env:` block, with the nearest declaration
winning. After the pane's commands finish, an interactive shell keeps running
inside the container. Use `container: docker@name` or a
`container: {type: ..., name: ..., user: ..., workdir: ...}` mapping to
address a specific instance; without an explicit name, the nearest `name` or
`label` in the layout is used. It is an error to resolve no name. The wrapper
assumes the pane shell provides a terminal.

Short complete layouts are available in the [`examples/`](../examples/)
directory:

- [`01-basic.yaml`](../examples/01-basic.yaml) — one workspace and editor pane.
- [`02-scoped-variables.yaml`](../examples/02-scoped-variables.yaml) — `env:`, and `locals:` expansion.
- [`03-inherited-commands.yaml`](../examples/03-inherited-commands.yaml) — workspace and tab commands bubbled to panes.
- [`04-global-scope.yaml`](../examples/04-global-scope.yaml) — shared `env:` exported to workspaces, with `locals:` and command expansion.
- [`05-container.yaml`](../examples/05-container.yaml) — pane commands running in a docker container.
- [`06-lxc.yaml`](../examples/06-lxc.yaml) — panes running inside LXC containers, with per-tab re-declaration.
- [`07-podman.yaml`](../examples/07-podman.yaml) — panes running inside podman containers via the full mapping form (`user:`/`workdir:`).

Preview the second layout with:

```sh
hs-smart-apply --print examples/02-scoped-variables.yaml
```
