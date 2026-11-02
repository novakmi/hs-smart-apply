"""Command line interface for hs-smart-apply."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml


_VARIABLE = re.compile(r"\$([a-zA-Z_]\w*)|\$\{\s*([a-zA-Z_]\w*)(?::?-([^}]*))?\s*\}")


def expand_str(value: Any, scope: dict[str, Any]) -> Any:
    """Expand tilde and shell-style variables using a layout scope."""
    if not isinstance(value, str):
        return value
    value = os.path.expanduser(value)

    def replace(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        default = match.group(3)
        current = scope.get(name)
        if current is not None and current != "":
            return str(current)
        if default is not None:
            return default
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


def process_node(
    node: Any,
    parent_env: dict[str, Any],
    parent_locals: dict[str, Any],
    parent_commands: list[str] | None = None,
    *,
    is_root: bool = True,
) -> Any:
    """Expand scopes and bubble ``commands`` values to leaf panes."""
    commands = list(parent_commands or [])
    if isinstance(node, list):
        return [process_node(item, parent_env, parent_locals, commands, is_root=False) for item in node]
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

    scope = {**env, **locals_}
    commands.extend(_node_commands(node, scope))

    result: dict[str, Any] = {}
    for key, value in node.items():
        if key in ("commands", "locals") or (key == "env" and is_root):
            continue
        if key == "env" and isinstance(value, dict):
            result[key] = {k: expand_str(v, scope) for k, v in value.items()}
        else:
            result[key] = process_node(value, env, locals_, commands, is_root=False)

    if not any(key in node for key in ("panes", "tabs", "workspaces")) and commands:
        result["command"] = " && ".join(commands)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Expand scoped variables and commands in a herdr-spreader YAML layout."
    )
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
        expanded = process_node(data, dict(os.environ), {})
    except (OSError, yaml.YAMLError) as exc:
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
