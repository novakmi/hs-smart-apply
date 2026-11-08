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
_RCFILE_PATH = "/tmp/.hs-smart-apply.rc"
_RCFILE_DELIMITER = "HS_SMART_APPLY_RC"


def expand_str(value: Any, scope: dict[str, Any]) -> Any:
    """Expand tilde and shell-style variables using a layout scope."""
    if not isinstance(value, str):
        return value
    value = os.path.expanduser(value)

    def replace(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        default = match.group(3)
        current = scope.get(name)
        if default is not None and (current is None or current == ""):
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
            raise ValueError(f"container user is only supported for docker and podman at {where}")
        return f"{ctype} exec -it --user {shlex.quote(user)} {shlex.quote(name)}"
    if ctype == "lxc":
        return f"lxc exec {shlex.quote(name)}"
    return f"{ctype} exec -it {shlex.quote(name)}"


def _container_command(
    spec: dict[str, str | None],
    commands: list[str],
    env: dict[str, Any],
    name: str,
    where: str,
) -> str:
    """Wrap leaf commands into a container exec that ends in an interactive shell."""
    base = _container_prefix(spec, name, where)
    workdir = spec.get("workdir")
    if not commands:
        if workdir:
            return f"{base} bash -c {shlex.quote(f'cd {shlex.quote(workdir)}; exec bash')}"
        return f"{base} bash"

    rc_lines = [
        f"trap 'rm -f {_RCFILE_PATH}' EXIT",
        'source "$HOME/.bashrc" 2>/dev/null || true',
        *[f"export {key}={shlex.quote(str(value))}" for key, value in env.items()],
    ]
    if workdir:
        rc_lines.append(f"cd {shlex.quote(workdir)}")
    rc_content = "\n".join(rc_lines) + "\n" + " &&\n".join(commands) + "\n"
    script = (
        f"cat > {_RCFILE_PATH} << '{_RCFILE_DELIMITER}'\n"
        f"{rc_content}"
        f"{_RCFILE_DELIMITER}\n"
        "exec bash --rcfile /tmp/.hs-smart-apply.rc"
    )
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
) -> Any:
    """Expand scopes, wrap container panes, export shared env, and bubble commands to panes."""
    commands = list(parent_commands or [])
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
                )
            )
        return processed
    if not isinstance(node, dict):
        return expand_str(node, {**parent_env, **parent_locals})

    env = parent_env.copy()
    locals_ = parent_locals.copy()
    raw_env = node.get("env")
    if isinstance(raw_env, dict):
        for key, value in raw_env.items():
            env[key] = expand_str(value, {**env, **locals_})

    raw_locals = node.get("locals")
    if isinstance(raw_locals, dict):
        # Evaluate in insertion order so later locals can refer to earlier ones.
        for key, value in raw_locals.items():
            locals_[key] = expand_str(value, {**env, **locals_})

    # Resolve forward references across both blocks against the full scope.
    if isinstance(raw_env, dict):
        for key, value in raw_env.items():
            env[key] = expand_str(value, {**env, **locals_})
    if isinstance(raw_locals, dict):
        for key, value in raw_locals.items():
            locals_[key] = expand_str(value, {**env, **locals_})

    scope = {**env, **locals_}
    where = f"layout{parent_path}"
    raw_container = node.get("container")
    container = _parse_container(raw_container, scope, where) if raw_container is not None else parent_container

    names = list(parent_names or [])
    own_name = node.get("name")
    if not isinstance(own_name, str):
        own_name = node.get("label")
    if isinstance(own_name, str):
        names.append(own_name)

    commands.extend(_node_commands(node, scope))

    result: dict[str, Any] = {}
    for key, value in node.items():
        if key in ("commands", "locals", "container") or (key == "env" and is_root):
            continue
        if key == "env" and isinstance(value, dict):
            result[key] = {k: expand_str(v, scope) for k, v in value.items()}
        else:
            result[key] = process_node(
                value,
                env,
                locals_,
                commands,
                is_root=False,
                parent_container=container,
                parent_names=names,
                parent_path=f"{parent_path}.{key}",
            )
            if is_root and key == "workspaces" and isinstance(result[key], list) and isinstance(raw_env, dict):
                # Export layout declarations, not the entire process environment.
                # Workspace declarations were already expanded in their own scope.
                shared_env = {name: env[name] for name in raw_env}
                for workspace in result[key]:
                    if isinstance(workspace, dict):
                        workspace["env"] = {**shared_env, **workspace.get("env", {})}

    if any(key in node for key in ("panes", "tabs", "workspaces")):
        return result
    if container is not None:
        name = _container_name(container, names, scope, where)
        result["command"] = _container_command(container, commands, env, name, where)
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        with args.layout.open(encoding="utf-8") as layout_file:
            data = yaml.safe_load(layout_file)
        expanded = process_node(data, {}, {})
    except (OSError, yaml.YAMLError, ValueError) as exc:
        print(f"hs-smart-apply: {exc}", file=sys.stderr)
        return 1

    if args.print or args.dry_run:
        yaml.safe_dump(expanded, sys.stdout, sort_keys=False)
        return 0

    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", encoding="utf-8", delete=False) as tmp:
            yaml.safe_dump(expanded, tmp, sort_keys=False)
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
