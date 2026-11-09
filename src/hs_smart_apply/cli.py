"""Command line interface for hs-smart-apply."""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

from ._version import __version__

_VARIABLE = re.compile(r"\$([a-zA-Z_]\w*)|\$\{\s*([a-zA-Z_]\w*)(?::?-([^}]*))?\s*\}")

_CONTAINER_TYPES = ("lxc", "docker", "podman")
_CONTAINER_SPEC_KEYS = {"type", "name", "user", "workdir"}


def expand_str(value: Any, scope: dict[str, Any]) -> Any:
    """Expand tilde and shell-style variables using a layout scope."""
    if not isinstance(value, str):
        return value
    value = os.path.expanduser(value)

    def replace(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        default = match.group(3)
        current = scope.get(name)
        if current is not None and default is not None and current == "":
            return default
        if current is not None:
            return str(current)
        return match.group(0)

    return _VARIABLE.sub(replace, value)


def _node_commands(node: dict[str, Any], scope: dict[str, Any]) -> list[str]:
    value = node.get("commands", [])
    if not isinstance(value, list):
        return []
    commands = []
    for item in value:
        if isinstance(item, str):
            commands.append(expand_str(item, scope))
        elif isinstance(item, dict) and isinstance(item.get("commands"), str):
            commands.append(expand_str(item["commands"], scope))
    return commands


def _node_after_clear_commands(node: dict[str, Any], scope: dict[str, Any]) -> list[str]:
    """Expand commands intended to run after the optional clear command."""
    value = node.get("commands_after_clear", [])
    if not isinstance(value, list):
        return []
    return [expand_str(item, scope) for item in value if isinstance(item, str)]


def _resolve_locals(raw_locals: Any, parent_locals: dict[str, Any], where: str) -> dict[str, Any]:
    """Resolve local references recursively, reporting cycles with their chain."""
    if not isinstance(raw_locals, dict):
        return parent_locals.copy()

    resolved = parent_locals.copy()
    resolving: list[str] = []
    completed: set[str] = set()

    def resolve(name: str) -> Any:
        if name in completed:
            return resolved[name]
        if name in resolved and name not in raw_locals:
            return resolved[name]
        if name in resolving:
            start = resolving.index(name)
            cycle = " -> ".join([*resolving[start:], name])
            raise ValueError(f"local variable cycle at {where}: {cycle}")
        if name not in raw_locals:
            return None

        resolving.append(name)
        value = raw_locals[name]
        if isinstance(value, str):
            def replace(match: re.Match[str]) -> str:
                ref = match.group(1) or match.group(2)
                default = match.group(3)
                if ref not in raw_locals and ref not in parent_locals:
                    return match.group(0)
                current = resolve(ref)
                if default is not None and (current is None or current == ""):
                    return default
                return str(current)

            value = _VARIABLE.sub(replace, value)
        resolving.pop()
        resolved[name] = value
        completed.add(name)
        return value

    for local_name in raw_locals:
        resolve(local_name)
    return resolved


def _resolve_environment(
    raw_env: dict[str, Any],
    process_env: dict[str, Any],
    where: str,
    *,
    preserve_unknown: bool = False,
) -> dict[str, Any]:
    """Resolve declared env references, optionally leaving runtime refs intact."""
    resolved: dict[str, Any] = {}
    resolving: list[str] = []

    def expand_home(value: str) -> str:
        if value == "~" or value.startswith("~/"):
            home = process_env.get("HOME") or os.path.expanduser("~")
            return f"{home}{value[1:]}"
        return os.path.expanduser(value)

    def preserve_home(value: str) -> str:
        if value == "~" or value.startswith("~/"):
            return f"${{HOME}}{value[1:]}"
        return value

    def resolve(name: str) -> Any:
        if name in resolved:
            return resolved[name]
        if name in resolving:
            start = resolving.index(name)
            cycle = " -> ".join([*resolving[start:], name])
            raise ValueError(f"environment variable cycle at {where}: {cycle}")

        value = raw_env[name]
        if not isinstance(value, str):
            resolved[name] = value
            return value

        if not preserve_unknown:
            value = expand_home(value)
        else:
            value = preserve_home(value)
        resolving.append(name)

        def replace(match: re.Match[str]) -> str:
            ref = match.group(1) or match.group(2)
            default = match.group(3)
            if ref in raw_env and ref != name:
                current = resolve(ref)
            elif ref in process_env:
                current = process_env[ref]
            elif ref == name and default is not None:
                current = None
            elif preserve_unknown:
                return match.group(0)
            else:
                current = None

            if current is None or (default is not None and current == ""):
                if default is not None and not preserve_unknown:
                    return default
                return match.group(0)
            return str(current)

        value = _VARIABLE.sub(replace, value)
        if not preserve_unknown:
            value = expand_home(value)
        else:
            value = preserve_home(value)
        resolving.pop()
        resolved[name] = value
        return value

    for variable in raw_env:
        resolve(variable)
    return resolved


def _shell_double_quote(value: Any) -> str:
    """Quote a container env value while preserving runtime shell expansion."""
    value = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`")
    return f'"{value}"'


def _parse_container(raw: Any, scope: dict[str, Any], where: str) -> dict[str, str | None]:
    """Validate a container declaration and resolve its values against `scope`."""
    spec: dict[str, str | None] = {}
    if isinstance(raw, str):
        expanded = expand_str(raw, scope)
        ctype, separator, name = expanded.partition("@")
        spec["type"] = ctype.strip()
        spec["name"] = name.strip() if separator and name.strip() else None
    elif isinstance(raw, dict):
        unknown = sorted(set(raw) - _CONTAINER_SPEC_KEYS)
        if unknown:
            raise ValueError(f"unsupported container keys {unknown} at {where}")
        ctype = raw.get("type")
        if not isinstance(ctype, str) or not ctype.strip():
            raise ValueError(f"container spec needs a 'type' key at {where}")
        spec["type"] = ctype.strip()
        for key in _CONTAINER_SPEC_KEYS - {"type"}:
            value = raw.get(key)
            spec[key] = None if value is None else str(expand_str(value, scope))
    else:
        raise ValueError(f"container must be '<type>@<name>', a type name, or a mapping at {where}; got {raw!r}")
    if spec["type"] not in _CONTAINER_TYPES:
        supported = ", ".join(_CONTAINER_TYPES)
        raise ValueError(f"unknown container type {spec['type']!r} at {where}; expected one of: {supported}")
    return spec


def _container_name(spec: dict[str, str | None], names: list[str], scope: dict[str, Any], where: str) -> str:
    """Resolve the container instance name: explicit spec name, then ancestor names."""
    for candidate in (spec.get("name"), *reversed(names)):
        if isinstance(candidate, str):
            resolved = expand_str(candidate, scope)
            if resolved:
                return resolved
    raise ValueError(
        f"no container name resolved at {where}; name the workspace/tab/pane or declare container: '<type>@<name>'"
    )


def _container_prefix(spec: dict[str, str | None], name: str, where: str) -> str:
    """Build the exec invocation prefix for the declared container type."""
    ctype = spec["type"] or ""
    user = spec.get("user")
    if user:
        if ctype == "lxc":
            # LXD starts exec as root by default; use sudo to select the user
            # while retaining the wrapper's environment and shell setup.
            return (
                f"lxc exec {shlex.quote(name)} -- sudo --user {shlex.quote(user)} "
                "--set-home --"
            )
        return f"{ctype} exec -it --user {shlex.quote(user)} {shlex.quote(name)}"
    if ctype == "lxc":
        return f"lxc exec {shlex.quote(name)} --"
    return f"{ctype} exec -it {shlex.quote(name)}"


def _container_command(
    spec: dict[str, str | None],
    commands: list[str],
    name: str,
    where: str,
) -> str:
    """Wrap leaf commands into a container exec that ends in an interactive shell."""
    base = _container_prefix(spec, name, where)
    workdir = spec.get("workdir")
    script_lines = [
        'source "$HOME/.bashrc" 2>/dev/null || true',
    ]
    if workdir:
        script_lines.append(f"cd {shlex.quote(workdir)}")
    script_lines.extend(commands)
    # Pass the setup directly to the container shell; no shared temporary
    # rcfile is needed, and the final shell inherits the setup environment.
    script_lines.append("exec bash -i")
    script = "\n".join(script_lines)
    return f"{base} bash -c {shlex.quote(script)}"


def process_node(
    node: Any,
    parent_env: dict[str, Any],
    parent_locals: dict[str, Any],
    parent_commands: list[str] | None = None,
    *,
    is_root: bool = True,
    parent_container: dict[str, str | None] | None = None,
    parent_names: list[str] | None = None,
    parent_path: str = "",
    process_environment: dict[str, Any] | None = None,
    parent_clear: bool = False,
    parent_after_clear_commands: list[str] | None = None,
) -> Any:
    """Expand scopes, wrap container panes, export shared env, and bubble commands to panes."""
    if process_environment is None:
        process_environment = parent_env if is_root else {}
        if is_root:
            parent_env = {}
    commands = list(parent_commands or [])
    after_clear_commands = list(parent_after_clear_commands or [])
    if isinstance(node, list):
        processed = []
        for index, item in enumerate(node):
            processed.append(
                process_node(
                    item,
                    parent_env,
                    parent_locals,
                    commands,
                    is_root=False,
                    parent_container=parent_container,
                    parent_names=parent_names,
                    parent_path=f"{parent_path}[{index}]",
                    process_environment=process_environment,
                    parent_clear=parent_clear,
                    parent_after_clear_commands=after_clear_commands,
                )
            )
        return processed
    if not isinstance(node, dict):
        env_scope = _resolve_environment(parent_env, process_environment, f"layout{parent_path}")
        return expand_str(node, {**env_scope, **parent_locals})

    env = parent_env.copy()
    raw_env = node.get("env")
    if isinstance(raw_env, dict):
        env.update(raw_env)
    raw_locals = node.get("locals")
    where = f"layout{parent_path}"
    locals_ = _resolve_locals(raw_locals, parent_locals, where)
    scope = locals_
    clear = node.get("clear", parent_clear)
    if not isinstance(clear, bool):
        raise ValueError(f"clear must be a boolean at {where}")
    raw_container = node.get("container")
    container = _parse_container(raw_container, scope, where) if raw_container is not None else parent_container
    if isinstance(raw_env, dict):
        # Herdr workspace env is not inherited by every split pane. Bubble
        # exports with the pane command so each shell, including container
        # shells, receives the scoped values before its setup commands run.
        shell_env = _resolve_environment(env, {}, where, preserve_unknown=True)
        for variable in raw_env:
            if not isinstance(variable, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", variable):
                raise ValueError(f"invalid environment variable name {variable!r} at {where}")
            commands.append(f"export {variable}={_shell_double_quote(shell_env[variable])}")

    names = list(parent_names or [])
    own_name = node.get("name")
    if not isinstance(own_name, str):
        own_name = node.get("label")
    if isinstance(own_name, str):
        names.append(own_name)

    commands.extend(_node_commands(node, scope))
    after_clear_commands.extend(_node_after_clear_commands(node, scope))

    result: dict[str, Any] = {}
    for key, value in node.items():
        if key in ("commands", "commands_after_clear", "locals", "container", "env", "clear"):
            continue
        result[key] = process_node(
            value,
            env,
            locals_,
            commands,
            is_root=False,
            parent_container=container,
            parent_names=names,
            parent_path=f"{parent_path}.{key}",
            process_environment=process_environment,
            parent_clear=clear,
            parent_after_clear_commands=after_clear_commands,
        )

    if any(key in node for key in ("panes", "tabs", "workspaces")):
        return result
    if clear:
        commands.append("clear")
    commands.extend(after_clear_commands)
    if container is not None:
        name = _container_name(container, names, scope, where)
        result["command"] = _container_command(container, commands, name, where)
    elif commands:
        result["command"] = " && ".join(commands)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hs-smart-apply", description="Expand scoped variables and commands in a herdr-spreader YAML layout."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("layout", type=Path, help="Path to the layout YAML file.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("-p", "--print", action="store_true", help="Print expanded YAML and exit.")
    mode.add_argument("-d", "--dry-run", action="store_true", help="Print expanded YAML without applying it.")
    return parser


class _LayoutDumper(yaml.SafeDumper):
    """Keep multiline shell commands readable in generated YAML."""


def _represent_layout_string(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_LayoutDumper.add_representer(str, _represent_layout_string)


def _dump_layout(data: Any, stream: Any) -> None:
    yaml.dump(data, stream, Dumper=_LayoutDumper, sort_keys=False)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        with args.layout.open(encoding="utf-8") as layout_file:
            data = yaml.safe_load(layout_file)
        expanded = process_node(data, os.environ, {})
    except (OSError, yaml.YAMLError, ValueError) as exc:
        print(f"hs-smart-apply: {exc}", file=sys.stderr)
        return 1

    if args.print or args.dry_run:
        _dump_layout(expanded, sys.stdout)
        return 0

    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", encoding="utf-8", delete=False) as tmp:
            _dump_layout(expanded, tmp)
            tmp_name = tmp.name
        completed = subprocess.run(["herdr-spreader", "apply", "--file", tmp_name], check=False)
        return completed.returncode
    except OSError as exc:
        print(f"hs-smart-apply: {exc}", file=sys.stderr)
        return 1
    finally:
        if tmp_name is not None:
            Path(tmp_name).unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
