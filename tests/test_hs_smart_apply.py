"""Pytest coverage for layout expansion and the command line interface."""

import shlex
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from hs_smart_apply.cli import expand_str, main, process_node


@pytest.mark.parametrize(
    ("value", "scope", "expected"),
    [
        ("$USER", {"USER": "michal"}, "michal"),
        ("${USER}", {"USER": "michal"}, "michal"),
        ("$USER", {"USER": ""}, ""),
        ("${USER}", {"USER": ""}, ""),
        ("venv${VENV_SUFFIX}", {"VENV_SUFFIX": ""}, "venv"),
        ("venv${VENV_SUFFIX}", {"VENV_SUFFIX": "-dev"}, "venv-dev"),
        ("${USER:-guest}", {}, "${USER:-guest}"),
        ("${USER:-guest}", {"USER": ""}, "guest"),
        ("${USER:-guest}", {"USER": "michal"}, "michal"),
        ("$UNSET", {}, "$UNSET"),
        (42, {}, 42),
    ],
)
def test_expand_str(value, scope, expected) -> None:
    assert expand_str(value, scope) == expected


def test_expand_str_expands_tilde(monkeypatch) -> None:
    monkeypatch.setenv("HOME", "/home/tester")
    assert expand_str("~/work", {}) == "/home/tester/work"


def test_locals_evaluate_in_order_and_inherit_parent_scope() -> None:
    layout = {
        "locals": {"first": "$HOME/work", "second": "$first/project"},
        "panes": [{"commands": ["echo $second"]}],
    }

    result = process_node(layout, {"HOME": "/home/user"}, {})

    assert result["panes"][0]["command"] == "echo $HOME/work/project"


def test_locals_resolve_recursively_and_reject_cycles() -> None:
    result = process_node(
        {"locals": {"root": "/work", "src": "${root}/src", "test": "${src}/test"}, "panes": [{"commands": ["cd ${test}"]}]},
        {},
        {},
    )
    assert result["panes"][0]["command"] == "cd /work/src/test"

    with pytest.raises(ValueError, match=r"local variable cycle.*a -> b -> a"):
        process_node({"locals": {"a": "${b}", "b": "${a}"}, "panes": [{}]}, {}, {})


@pytest.mark.parametrize("suffix", [None, "", "-dev"])
def test_env_values_become_runtime_exports_with_declared_references_resolved(suffix) -> None:
    layout = {
        "env": {
            "VENV_SUFFIX": suffix or "",
            "TAILF_DIR": "${HOME}/vol/cn/tailf",
            "VENV_DIR": "${TAILF_DIR}/venv-pool/venv${VENV_SUFFIX}",
        },
        "workspaces": [{"tabs": [{"panes": [{"commands": ['source "${VENV_DIR}/sysval/bin/activate"']}]}]}],
    }

    result = process_node(layout, {}, {})

    workspace = result["workspaces"][0]
    assert "env" not in workspace
    command = workspace["tabs"][0]["panes"][0]["command"]
    assert command.startswith(
        f'export VENV_SUFFIX="{suffix or ""}" && '
        'export TAILF_DIR="${HOME}/vol/cn/tailf" && '
        f'export VENV_DIR="${{HOME}}/vol/cn/tailf/venv-pool/venv{suffix or ""}" && '
        'source "${VENV_DIR}/sysval/bin/activate"'
    )


def test_locals_and_env_are_separate_but_both_export_to_pane_shells() -> None:
    layout = yaml.safe_load("""
locals:
  relative_root: projects/app
  api_path: ${relative_root}/api
env:
  APP_ROOT: ${HOME}/projects/app
workspaces:
  - name: api
    locals:
      log_path: ${api_path}/logs
    env:
      LOG_DIR: ${APP_ROOT}/logs
    tabs:
      - panes:
          - commands:
              - echo ${APP_ROOT} ${api_path} ${LOG_DIR}
""")

    result = process_node(layout, {"HOME": "/home/tester"}, {})

    workspace = result["workspaces"][0]
    command = workspace["tabs"][0]["panes"][0]["command"]
    assert command.startswith(
        'export APP_ROOT="${HOME}/projects/app" && export LOG_DIR="${HOME}/projects/app/logs" && '
    )
    assert command.endswith("echo ${APP_ROOT} projects/app/api ${LOG_DIR}")
    assert "locals" not in workspace
    assert "env" not in result


def test_env_and_locals_remain_separate_scopes() -> None:
    layout = {
        "env": {"MODE": "shared"},
        "workspaces": [
            {
                "locals": {"inherited_mode": "$MODE"},
                "env": {"MODE": "workspace", "INHERITED_MODE": "$inherited_mode"},
            }
        ],
    }

    result = process_node(
        {**layout, "workspaces": [{**layout["workspaces"][0], "tabs": [{"panes": [{}]}]}]}, {}, {}
    )
    command = result["workspaces"][0]["tabs"][0]["panes"][0]["command"]
    assert command == 'export MODE="shared" && export MODE="workspace" && export INHERITED_MODE="$inherited_mode"'


def test_env_shell_exports_preserve_runtime_refs_and_locals_expand() -> None:
    layout = {
        "env": {"APP_ROOT": "$HOME/app", "MODE": "test"},
        "locals": {"API_DIR": "services/api"},
        "workspaces": [
            {
                "name": "api",
                "root": "${APP_ROOT}/api",
                "commands": ["echo $MODE"],
                "tabs": [{"panes": [{"commands": ["run"]}]}],
            }
        ],
    }

    result = process_node(layout, {"HOME": "/home/user"}, {})

    pane = result["workspaces"][0]["tabs"][0]["panes"][0]
    assert result["workspaces"][0]["root"] == "/home/user/app/api"
    assert "env" not in result["workspaces"][0]
    assert pane["command"] == (
        'export APP_ROOT="$HOME/app" && export MODE="test" && echo $MODE && run'
    )


def test_shared_environment_exports_into_panes_without_leaking_process_env_or_locals() -> None:
    layout = {
        "env": {"APP_ROOT": "$HOME/app", "MODE": "shared", "OPTIONAL": ""},
        "locals": {"PRIVATE_LOCAL": "preprocessor-only"},
        "workspaces": [
            {
                "name": "api",
                "env": {"MODE": "api", "CACHE": "$APP_ROOT/cache"},
                "tabs": [{"panes": [{"commands": ["printenv MODE"]}]}],
            },
            {"name": "worker", "tabs": [{"panes": [{}]}]},
        ],
    }

    result = process_node(layout, {"HOME": "/home/user", "PROCESS_ONLY": "private"}, {})

    assert "env" not in result
    api, worker = result["workspaces"]
    assert "env" not in api and "env" not in worker
    api_command = api["tabs"][0]["panes"][0]["command"]
    worker_command = worker["tabs"][0]["panes"][0]["command"]
    assert api_command == (
        'export APP_ROOT="$HOME/app" && export MODE="shared" && export OPTIONAL="" && '
        'export MODE="api" && export CACHE="$HOME/app/cache" && printenv MODE'
    )
    assert worker_command == 'export APP_ROOT="$HOME/app" && export MODE="shared" && export OPTIONAL=""'


def test_env_references_expand_recursively_and_report_cycles() -> None:
    result = process_node(
        {
            "env": {"ROOT": "${HOME}/project", "APP": "${ROOT}/app"},
            "workspaces": [{"env": {"CACHE": "${APP}/cache"}, "tabs": [{"panes": [{}]}]}],
        },
        {"HOME": "/home/tester"},
        {},
    )
    command = result["workspaces"][0]["tabs"][0]["panes"][0]["command"]
    assert command == (
        'export ROOT="${HOME}/project" && export APP="${HOME}/project/app" && '
        'export CACHE="${HOME}/project/app/cache"'
    )

    with pytest.raises(ValueError, match=r"environment variable cycle.*A -> B -> A"):
        process_node({"env": {"A": "$B", "B": "$A"}, "workspaces": [{}]}, {}, {})


def test_layout_root_expands_env_references() -> None:
    result = process_node(
        {"env": {"APP_ROOT": "${HOME}/app"}, "workspaces": [{"root": "${APP_ROOT}/api", "panes": [{}]}]},
        {"HOME": "/home/tester"},
        {},
    )

    assert result["workspaces"][0]["root"] == "/home/tester/app/api"


def test_env_exports_expand_tilde_in_the_pane_shell() -> None:
    result = process_node(
        {
            "env": {"PROJECT_ROOT": "~/code/app"},
            "workspaces": [{"root": "$PROJECT_ROOT", "panes": [{}]}],
        },
        {"HOME": "/home/tester"},
        {},
    )

    command = result["workspaces"][0]["panes"][0]["command"]
    assert command == 'export PROJECT_ROOT="${HOME}/code/app"'
    assert result["workspaces"][0]["root"] == "/home/tester/code/app"


def test_workspace_environment_overrides_shared_values_with_empty_strings() -> None:
    layout = {"env": {"MODE": "shared"}, "workspaces": [{"env": {"MODE": ""}}]}

    result = process_node({**layout, "workspaces": [{"env": {"MODE": ""}, "panes": [{}]}]}, {}, {})
    assert result["workspaces"][0]["panes"][0]["command"] == 'export MODE="shared" && export MODE=""'


def test_process_environment_alone_is_not_exported_to_workspaces() -> None:
    layout = {"workspaces": [{"name": "api"}]}

    assert process_node(layout, {"PROCESS_ONLY": "private"}, {}) == layout


def test_environment_and_locals_are_scoped_to_children() -> None:
    layout = {
        "tabs": [
            {"env": {"APP": "editor"}, "panes": [{"commands": ["run $APP"]}]},
            {"panes": [{"commands": ["run $APP"]}]},
        ]
    }

    result = process_node(layout, {}, {})

    assert result["tabs"][0]["panes"][0]["command"] == 'export APP="editor" && run $APP'
    assert result["tabs"][1]["panes"][0]["command"] == "run $APP"


def test_environment_values_inherit_and_override_across_layout_levels() -> None:
    layout = {
        "workspaces": [
            {
                "env": {"APP_MODE": "workspace"},
                "tabs": [
                    {
                        "env": {"APP_MODE": "tab"},
                        "panes": [{"env": {"APP_MODE": "pane"}, "commands": ["echo $APP_MODE"]}],
                    }
                ],
            }
        ]
    }

    result = process_node(layout, {}, {})

    workspace = result["workspaces"][0]
    tab = workspace["tabs"][0]
    pane = tab["panes"][0]
    assert "env" not in workspace and "env" not in tab and "env" not in pane
    assert pane["command"] == (
        'export APP_MODE="workspace" && export APP_MODE="tab" && '
        'export APP_MODE="pane" && echo $APP_MODE'
    )


def test_environment_exports_are_bubbled_to_every_pane() -> None:
    layout = {
        "env": {"ROOT": "${HOME}/project"},
        "workspaces": [
            {
                "env": {"MODE": "workspace"},
                "tabs": [
                    {
                        "env": {"TAB_VALUE": "tab"},
                        "panes": [
                            {},
                            {"split": "right", "env": {"PANE_VALUE": "pane"}},
                        ],
                    }
                ],
            }
        ],
    }

    result = process_node(layout, {"HOME": "/home/tester"}, {})
    panes = result["workspaces"][0]["tabs"][0]["panes"]

    assert panes[0]["command"] == (
        'export ROOT="${HOME}/project" && export MODE="workspace" && export TAB_VALUE="tab"'
    )
    assert panes[1]["command"] == (
        'export ROOT="${HOME}/project" && export MODE="workspace" && export TAB_VALUE="tab" && '
        'export PANE_VALUE="pane"'
    )


def test_commands_bubble_from_ancestors_to_each_pane() -> None:
    layout = {
        "workspaces": [
            {
                "commands": ["prepare"],
                "tabs": [
                    {
                        "commands": ["open $APP"],
                        "env": {"APP": "editor"},
                        "panes": [{"commands": ["run"]}, {"commands": ["inspect"]}],
                    }
                ],
            }
        ],
    }

    result = process_node(layout, {}, {})

    workspace = result["workspaces"][0]
    tab = workspace["tabs"][0]
    assert tab["panes"][0]["command"] == 'prepare && export APP="editor" && open $APP && run'
    assert tab["panes"][1]["command"] == 'prepare && export APP="editor" && open $APP && inspect'
    assert "commands" not in workspace
    assert "commands" not in tab


def test_clear_inherits_to_panes_and_can_be_disabled_locally() -> None:
    layout = {
        "workspaces": [
            {
                "clear": True,
                "commands": ["workspace setup"],
                "tabs": [
                    {
                        "commands": ["tab setup"],
                        "panes": [
                            {"commands": ["pane setup"]},
                            {"split": "right", "clear": False, "commands": ["other pane"]},
                        ],
                    }
                ],
            }
        ]
    }

    workspace = process_node(layout, {}, {})["workspaces"][0]
    panes = workspace["tabs"][0]["panes"]

    assert panes[0]["command"] == "workspace setup && tab setup && pane setup && clear"
    assert panes[1]["command"] == "workspace setup && tab setup && other pane"
    assert "clear" not in workspace and "clear" not in workspace["tabs"][0]


def test_clear_from_tab_applies_only_to_its_panes() -> None:
    layout = {
        "tabs": [
            {"clear": True, "panes": [{"commands": ["setup"]}]},
            {"panes": [{"commands": ["leave visible"]}]},
        ]
    }

    tabs = process_node(layout, {}, {})["tabs"]

    assert tabs[0]["panes"][0]["command"] == "setup && clear"
    assert tabs[1]["panes"][0]["command"] == "leave visible"


def test_clear_at_top_level_inherits_to_workspaces() -> None:
    layout = {
        "clear": True,
        "workspaces": [
            {"tabs": [{"panes": [{"commands": ["setup"]}]}]},
            {"clear": False, "tabs": [{"panes": [{"commands": ["leave visible"]}]}]},
        ],
    }

    result = process_node(layout, {}, {})

    assert result["workspaces"][0]["tabs"][0]["panes"][0]["command"] == "setup && clear"
    assert result["workspaces"][1]["tabs"][0]["panes"][0]["command"] == "leave visible"
    assert "clear" not in result


def test_commands_after_clear_inherit_and_run_even_when_clear_is_disabled() -> None:
    layout = {
        "clear": True,
        "commands_after_clear": ["echo workspace ready"],
        "workspaces": [
            {
                "commands_after_clear": ["echo workspace message"],
                "tabs": [
                    {
                        "commands_after_clear": ["echo tab message"],
                        "panes": [
                            {
                                "commands": ["setup"],
                                "clear": False,
                                "commands_after_clear": ["echo pane message"],
                            }
                        ],
                    }
                ],
            }
        ],
    }

    pane = process_node(layout, {}, {})["workspaces"][0]["tabs"][0]["panes"][0]

    assert pane["command"] == (
        "setup && echo workspace ready && echo workspace message && "
        "echo tab message && echo pane message"
    )
    assert "commands_after_clear" not in pane


def test_commands_after_clear_runs_after_clear_in_container() -> None:
    layout = {
        "container": "lxc@db",
        "clear": True,
        "commands": ["setup"],
        "commands_after_clear": ["echo container ready"],
        "panes": [{}],
    }

    pane = process_node(layout, {}, {})["panes"][0]
    script = (
        'source "$HOME/.bashrc" 2>/dev/null || true\n'
        "setup\nclear\necho container ready\nexec bash -i"
    )

    assert pane["command"] == f"lxc exec db -- bash -c {shlex.quote(script)}"


def test_non_list_commands_are_ignored() -> None:
    result = process_node({"commands": "echo ignored", "panes": [{"root": "/tmp"}]}, {}, {})

    assert "command" not in result["panes"][0]


def test_multiline_heredoc_command_is_preserved_and_expanded() -> None:
    heredoc = "docker exec app bash << 'EOF'\necho $MESSAGE\nEOF"

    result = process_node(
        {"workspaces": [{"tabs": [{"panes": [{"commands": [heredoc]}]}]}]},
        {"MESSAGE": "hello"},
        {},
    )

    assert result["workspaces"][0]["tabs"][0]["panes"][0]["command"] == (
        "docker exec app bash << 'EOF'\necho $MESSAGE\nEOF"
    )


def test_all_example_layouts_process_and_remove_preprocessor_keys() -> None:
    examples_dir = Path(__file__).parents[1] / "examples"

    for example in sorted(examples_dir.glob("*.yaml")):
        raw_layout = yaml.safe_load(example.read_text(encoding="utf-8"))
        expanded = process_node(raw_layout, {}, {})

        assert "workspaces" in expanded, example.name
        assert "env" not in expanded, example.name

        def assert_no_preprocessor_keys(value, example_name=example.name):
            if isinstance(value, dict):
                assert "locals" not in value, example_name
                assert "commands" not in value, example_name
                assert "container" not in value, example_name
                for child in value.values():
                    assert_no_preprocessor_keys(child)
            elif isinstance(value, list):
                for child in value:
                    assert_no_preprocessor_keys(child)

        assert_no_preprocessor_keys(expanded)


def write_layout(tmp_path: Path, contents: str) -> Path:
    layout = tmp_path / "layout.yaml"
    layout.write_text(contents, encoding="utf-8")
    return layout


def test_print_mode_expands_yaml(tmp_path, capsys) -> None:
    layout = write_layout(tmp_path, "panes:\n  - commands:\n      - echo ${NAME:-world}\n")

    assert main(["--print", str(layout)]) == 0
    assert "echo ${NAME:-world}" in capsys.readouterr().out


def test_print_mode_preserves_host_shell_refs_in_env_exports(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.setenv("HOME", "/home/tester")
    layout = write_layout(
        tmp_path,
        "env:\n  ROOT: '${HOME}/project'\n  APP: '${ROOT}/app'\nworkspaces:\n  - env:\n      CACHE: '${APP}/cache'\n    panes:\n      - {}\n",
    )

    assert main(["--print", str(layout)]) == 0
    output = yaml.safe_load(capsys.readouterr().out)
    command = output["workspaces"][0]["panes"][0]["command"]
    assert command == (
        'export ROOT="${HOME}/project" && export APP="${HOME}/project/app" && '
        'export CACHE="${HOME}/project/app/cache"'
    )


def test_print_mode_exports_shared_environment_to_workspaces(tmp_path, capsys) -> None:
    layout = write_layout(
        tmp_path,
        "env:\n  MODE: shared\nworkspaces:\n  - name: api\n    env:\n      MODE: api\n    panes:\n      - {}\n  - name: worker\n    panes:\n      - {}\n",
    )

    assert main(["--print", str(layout)]) == 0

    output = yaml.safe_load(capsys.readouterr().out)
    api, worker = output["workspaces"]
    assert "env" not in api and "env" not in worker
    assert api["panes"][0]["command"] == 'export MODE="shared" && export MODE="api"'
    assert worker["panes"][0]["command"] == 'export MODE="shared"'


def test_dry_run_prints_yaml_without_invoking_apply(tmp_path, capsys, monkeypatch) -> None:
    layout = write_layout(tmp_path, "panes: []\n")
    run = Mock(side_effect=AssertionError("apply must not run in dry-run mode"))
    monkeypatch.setattr("hs_smart_apply.cli.subprocess.run", run)

    assert main(["--dry-run", str(layout)]) == 0
    assert "panes: []" in capsys.readouterr().out
    run.assert_not_called()


def test_apply_passes_temporary_yaml_and_returns_child_status(tmp_path, monkeypatch) -> None:
    layout = write_layout(tmp_path, "panes: []\n")
    observed = {}

    def fake_run(command, check):
        observed["command"] = command
        observed["check"] = check
        observed["yaml"] = Path(command[-1]).read_text(encoding="utf-8")
        observed["temp_path"] = Path(command[-1])
        return Mock(returncode=7)

    monkeypatch.setattr("hs_smart_apply.cli.subprocess.run", fake_run)

    assert main([str(layout)]) == 7
    assert observed["command"][:3] == ["herdr-spreader", "apply", "--file"]
    assert observed["check"] is False
    assert yaml.safe_load(observed["yaml"]) == {"panes": []}
    assert not observed["temp_path"].exists()


def test_missing_layout_reports_error(tmp_path, capsys) -> None:
    assert main([str(tmp_path / "missing.yaml")]) == 1
    assert "hs-smart-apply:" in capsys.readouterr().err


def test_invalid_yaml_reports_error(tmp_path, capsys) -> None:
    layout = write_layout(tmp_path, "panes: [\n")

    assert main(["--print", str(layout)]) == 1
    assert "hs-smart-apply:" in capsys.readouterr().err


def test_apply_reports_missing_executable(tmp_path, monkeypatch, capsys) -> None:
    layout = write_layout(tmp_path, "panes: []\n")
    monkeypatch.setattr("hs_smart_apply.cli.subprocess.run", Mock(side_effect=FileNotFoundError("not found")))

    assert main([str(layout)]) == 1
    assert "not found" in capsys.readouterr().err


def test_print_and_dry_run_cannot_be_combined(tmp_path) -> None:
    layout = write_layout(tmp_path, "panes: []\n")

    with pytest.raises(SystemExit) as error:
        main(["--print", "--dry-run", str(layout)])

    assert error.value.code == 2


def test_help_lists_supported_cli_options(capsys) -> None:
    with pytest.raises(SystemExit) as error:
        main(["--help"])

    assert error.value.code == 0
    output = capsys.readouterr().out
    assert "--print" in output
    assert "--dry-run" in output


def test_version_option_prints_package_version(capsys) -> None:
    from hs_smart_apply import __version__

    with pytest.raises(SystemExit) as error:
        main(["--version"])

    assert error.value.code == 0
    assert capsys.readouterr().out.strip() == f"hs-smart-apply {__version__}"


def test_env_references_resolve_within_env_but_not_from_locals() -> None:
    layout = {
        "workspaces": [
            {
                "env": {"MODE": "$APP"},
                "locals": {"APP": "editor"},
                "tabs": [
                    {
                        "env": {"FOO": "$BAR", "BAR": "x"},
                        "panes": [{"commands": ["echo $MODE,$FOO"]}],
                    }
                ],
            }
        ]
    }

    result = process_node(layout, {}, {})

    workspace = result["workspaces"][0]
    assert "env" not in workspace and "env" not in workspace["tabs"][0]
    assert workspace["tabs"][0]["panes"][0]["command"] == (
        'export MODE="$APP" && export FOO="x" && export BAR="x" && echo $MODE,$FOO'
    )


def test_container_env_paths_expand_inside_container_shell() -> None:
    layout = {
        "workspaces": [
            {
                "container": "lxc@dev",
                "env": {
                    "TAILF_DIR": "${HOME}/vol/cn/tailf",
                    "SYSVAL_DIR": "${TAILF_DIR}/src/sysval",
                },
                "panes": [{}],
            }
        ]
    }

    result = process_node(layout, {"HOME": "/host/home"}, {})

    workspace = result["workspaces"][0]
    command = workspace["panes"][0]["command"]
    assert 'export TAILF_DIR="${HOME}/vol/cn/tailf"' in command
    assert 'export SYSVAL_DIR="${HOME}/vol/cn/tailf/src/sysval"' in command
    assert "env" not in workspace


def test_undeclared_process_environment_is_not_expanded(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setenv("SMART_PROCESS_ONLY", "from-shell")
    layout = write_layout(tmp_path, "panes:\n  - commands:\n      - echo $SMART_PROCESS_ONLY\n")

    assert main(["--print", str(layout)]) == 0
    assert "echo $SMART_PROCESS_ONLY" in capsys.readouterr().out

    result = process_node({"panes": [{"commands": ["echo $SMART_PROCESS_ONLY"]}]}, {}, {})

    assert result["panes"][0]["command"] == "echo $SMART_PROCESS_ONLY"


def test_container_declaration_wraps_pane_commands() -> None:
    layout = {
        "workspaces": [
            {
                "name": "web",
                "container": "docker@web",
                "env": {"MSG": "hi"},
                "commands": ["echo $MSG"],
                "tabs": [{"panes": [{"commands": ["run"]}]}],
            }
        ]
    }

    result = process_node(layout, {}, {})

    workspace = result["workspaces"][0]
    pane = workspace["tabs"][0]["panes"][0]
    script = (
        'source "$HOME/.bashrc" 2>/dev/null || true\n'
        'export MSG="hi"\n'
        "echo $MSG\n"
        "run\n"
        "exec bash -i"
    )

    assert pane["command"] == f"docker exec -it web bash -c {shlex.quote(script)}"
    assert "container" not in workspace


@pytest.mark.parametrize(
    ("ctype", "prefix"),
    [
        ("lxc", "lxc exec node1 --"),
        ("docker", "docker exec -it node1"),
        ("podman", "podman exec -it node1"),
    ],
)
def test_container_type_maps_to_exec_prefix(ctype, prefix) -> None:
    result = process_node({"container": ctype, "panes": [{"name": "node1", "commands": ["echo hi"]}]}, {}, {})

    assert result["panes"][0]["command"].startswith(f"{prefix} bash -c ")


def test_container_without_commands_opens_interactive_shell() -> None:
    layout = {"container": "lxc@db", "env": {"MODE": "test"}, "panes": [{}]}

    result = process_node(layout, {}, {})

    script = 'source "$HOME/.bashrc" 2>/dev/null || true\nexport MODE="test"\nexec bash -i'
    assert result["panes"][0]["command"] == f"lxc exec db -- bash -c {shlex.quote(script)}"


def test_clear_runs_inside_container_before_interactive_shell() -> None:
    layout = {"container": "lxc@db", "clear": True, "panes": [{"commands": ["setup"]}]}

    result = process_node(layout, {}, {})

    script = 'source "$HOME/.bashrc" 2>/dev/null || true\nsetup\nclear\nexec bash -i'
    assert result["panes"][0]["command"] == f"lxc exec db -- bash -c {shlex.quote(script)}"


def test_container_names_fall_back_to_nearest_ancestor_label() -> None:
    layout = {"tabs": [{"label": "server", "container": "podman", "panes": [{"commands": ["run"]}]}]}

    result = process_node(layout, {}, {})

    command = result["tabs"][0]["panes"][0]["command"]
    assert command.startswith("podman exec -it server bash -c ")
    assert "run\n" in command


def test_container_mapping_places_user_and_workdir_in_container() -> None:
    layout = {
        "container": {"type": "docker", "name": "cache", "user": "postgres", "workdir": "/data"},
        "panes": [{"commands": ["redis-cli ping"]}],
    }

    result = process_node(layout, {}, {})

    command = result["panes"][0]["command"]
    assert command.startswith("docker exec -it --user postgres cache bash -c ")
    assert "cd /data\n" in command
    assert "redis-cli ping\n" in command


def test_container_workdir_without_commands_runs_interactive_shell_there() -> None:
    layout = {"container": {"type": "podman", "name": "db", "workdir": "/var/lib/db"}, "panes": [{"name": "db"}]}

    result = process_node(layout, {}, {})

    script = 'source "$HOME/.bashrc" 2>/dev/null || true\ncd /var/lib/db\nexec bash -i'
    assert result["panes"][0]["command"] == f"podman exec -it db bash -c {shlex.quote(script)}"


def test_missing_container_name_reports_error(tmp_path, capsys) -> None:
    layout = write_layout(tmp_path, "container: docker\npanes:\n  - {}\n")

    assert main(["--print", str(layout)]) == 1
    assert "no container name resolved" in capsys.readouterr().err


def test_unknown_container_type_reports_error(tmp_path, capsys) -> None:
    layout = write_layout(tmp_path, "container: dockerx\npanes: []\n")

    assert main(["--print", str(layout)]) == 1
    assert "dockerx" in capsys.readouterr().err


def test_lxc_container_user_runs_shell_as_user(tmp_path, capsys) -> None:
    layout = write_layout(tmp_path, "container: {type: lxc, name: db, user: postgres}\npanes:\n  - {}\n")

    assert main(["--print", str(layout)]) == 0
    assert "lxc exec db -- sudo --user postgres --set-home -- bash -c" in capsys.readouterr().out
