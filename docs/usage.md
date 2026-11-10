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

To use the source in a checkout without installing hs-smart-apply, run these
commands from the project root with Python 3.12 or newer and PyYAML available
in that Python environment:

```sh
PYTHONPATH=src python -m hs_smart_apply --print examples/05-container.yaml
PYTHONPATH=src python -m hs_smart_apply --dry-run layout.yaml
PYTHONPATH=src python -m hs_smart_apply layout.yaml
```

The module is loaded from `src/`, so this uses the checkout's latest code,
including changes that are not in your installed version. `--print` and
`--dry-run` display the expanded YAML. The final command applies the layout to
the active Herdr session and requires `herdr-spreader` on `PATH`.
From another directory, use the module name with `-m` and point `PYTHONPATH`
to the checkout's `src/` directory:

```sh
PYTHONPATH="$HOME/vol/src/projects/hs-smart-apply/src" \
  python3 -m hs_smart_apply "$HOME/vol/cn/tailf/herdr/sysval.yaml"
```

Layout nodes may define `env:` and `locals:` mappings, and child nodes inherit
those values. `env:` entries become shell `export` commands on each pane.
References between declared env values resolve recursively into those exports;
shell references such as `$HOME` expand when the pane command runs. Forward
references work, and cyclic env references report an error. `locals:` values
are recursively expanded against locals declared in the same or parent nodes.
Cyclic local references also report an error. Locals are expansion-only and
never exported. For container panes, env exports run inside the container
shell, so `$HOME` uses the container user's home. `commands:` accepts a list
of strings; commands inherited from ancestor nodes are joined with `&&` on
each leaf pane.
Quote entries that contain `"label: value"`-shaped text (`'echo "tag: v2"'`),
otherwise YAML parses them as mappings and they are ignored.

The exports are placed in each pane command because Herdr does not reliably
carry workspace env into split panes. This also ensures the same declarations
run in the shell context that owns them: the host for regular panes and the
container for container panes. The generated layout has no `env:` fields.

`locals:` are resolved independently of `env:`. A local that refers to an env
variable keeps that reference as written. Use `env:` values for runtime shell
settings and `locals:` for preprocessing-only values.

```yaml
env:
  APP_ROOT: "${HOME}/projects/app"
locals:
  project_path: projects/app
workspaces:
  - name: api
    root: "${project_path}"
    tabs:
      - panes:
          - commands:
              - echo "Project path is ${project_path}"
```

Env declarations at every level are folded into each pane's command, in scope
order. A closer declaration overrides an inherited value, including an empty
string. The generated Herdr layout contains no `env:` fields:

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

The generated pane command exports `APP_ROOT` and the overridden `MODE` before
running its setup commands. The workspace root is resolved from the env value
before Herdr receives the layout.

Set `clear: true` at the top level, workspace, tab, or pane to append `clear`
after each affected pane's setup commands. The setting inherits into child
panes; a closer `clear: false` disables it for that subtree. Container panes
run `clear` inside the container immediately before opening the interactive
shell. Use `commands_after_clear:` at any of those levels for commands to run
after the optional `clear`. These commands inherit and accumulate down the
layout, and still run when `clear: false`; container panes run them inside the
container too.

Layout nodes may also declare `container:` to run their commands inside a
container. Allowed types are `lxc`, `docker`, and `podman`; the declaration
inherits to child panes like an `env:` block, with the nearest declaration
winning. After the pane's commands finish, an interactive shell keeps running
inside the container. Use `container: docker@name` or a
`container: {type: ..., name: ..., user: ..., workdir: ...}` mapping to
address a specific instance; without an explicit name, the nearest `name` or
`label` in the layout is used. For LXC, `user:` selects the account through
`sudo` inside the instance. For Docker and Podman, it uses their native user
option. It is an error to resolve no name. The wrapper assumes the pane shell
provides a terminal.

Short complete layouts are available in the [`examples/`](../examples/)
directory:

- [`01-basic.yaml`](../examples/01-basic.yaml) — one workspace and editor pane.
- [`02-scoped-variables.yaml`](../examples/02-scoped-variables.yaml) — `env:`, and `locals:` expansion.
- [`03-inherited-commands.yaml`](../examples/03-inherited-commands.yaml) — workspace and tab commands bubbled to panes.
- [`04-global-scope.yaml`](../examples/04-global-scope.yaml) — shared `env:` exported in each pane command, with `locals:` and command expansion.
- [`05-container.yaml`](../examples/05-container.yaml) — pane commands running in a docker container.
- [`06-lxc.yaml`](../examples/06-lxc.yaml) — panes running inside LXC containers, with per-tab re-declaration.
- [`07-podman.yaml`](../examples/07-podman.yaml) — panes running inside podman containers via the full mapping form (`user:`/`workdir:`).

Preview the second layout with:

```sh
hs-smart-apply --print examples/02-scoped-variables.yaml
```
