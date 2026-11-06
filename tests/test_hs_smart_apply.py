"""Pytest coverage for layout expansion and the command line interface."""

import os
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
        ("${USER:-guest}", {}, "guest"),
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

    assert result["panes"][0]["command"] == "echo /home/user/work/project"


def test_top_level_env_and_locals_expand_workspaces_and_are_consumed() -> None:
    layout = {
        "env": {"APP_ROOT": "$HOME/app", "MODE": "test"},
        "locals": {"API_DIR": "$APP_ROOT/api"},
        "workspaces": [
            {
                "name": "api",
                "root": "$API_DIR",
                "commands": ["echo $MODE"],
                "tabs": [{"panes": [{"commands": ["run"]}]}],
            }
        ],
    }

    result = process_node(layout, {"HOME": "/home/user"}, {})

    assert result == {
        "workspaces": [
            {
                "name": "api",
                "root": "/home/user/app/api",
                "env": {"APP_ROOT": "/home/user/app", "MODE": "test"},
                "tabs": [{"panes": [{"command": "echo test && run"}]}],
            }
        ]
    }


def test_shared_environment_reaches_workspaces_without_exporting_process_env_or_locals() -> None:
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
    assert api["env"] == {
        "APP_ROOT": "/home/user/app",
        "MODE": "api",
        "OPTIONAL": "",
        "CACHE": "/home/user/app/cache",
    }
    assert worker["env"] == {"APP_ROOT": "/home/user/app", "MODE": "shared", "OPTIONAL": ""}
    assert api["tabs"][0]["panes"][0]["command"] == "printenv MODE"
    assert "env" not in layout["workspaces"][1]


def test_workspace_environment_overrides_shared_values_with_empty_strings() -> None:
    layout = {"env": {"MODE": "shared"}, "workspaces": [{"env": {"MODE": ""}}]}

    assert process_node(layout, {}, {})["workspaces"][0]["env"] == {"MODE": ""}


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

    assert result["tabs"][0]["panes"][0]["command"] == "run editor"
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
    assert workspace["env"]["APP_MODE"] == "workspace"
    assert tab["env"]["APP_MODE"] == "tab"
    assert pane["env"]["APP_MODE"] == "pane"
    assert pane["command"] == "echo pane"


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
    assert tab["panes"][0]["command"] == "prepare && open editor && run"
    assert tab["panes"][1]["command"] == "prepare && open editor && inspect"
    assert "commands" not in workspace
    assert "commands" not in tab


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
        "docker exec app bash << 'EOF'\necho hello\nEOF"
    )


def test_all_example_layouts_process_and_remove_preprocessor_keys(monkeypatch) -> None:
    examples_dir = Path(__file__).parents[1] / "examples"
    monkeypatch.setenv("PROJECT_ROOT", "/tmp/web-app")
    monkeypatch.setenv("NODE_ENV", "test")

    for example in sorted(examples_dir.glob("*.yaml")):
        raw_layout = yaml.safe_load(example.read_text(encoding="utf-8"))
        expanded = process_node(raw_layout, dict(os.environ), {})

        assert "workspaces" in expanded, example.name
        assert "env" not in expanded, example.name

        def assert_no_preprocessor_keys(value, example_name=example.name):
            if isinstance(value, dict):
                assert "locals" not in value, example_name
                assert "commands" not in value, example_name
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
    assert "echo world" in capsys.readouterr().out


def test_print_mode_exports_shared_environment_to_workspaces(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.setenv("PROCESS_ONLY", "private")
    layout = write_layout(
        tmp_path,
        "env:\n  MODE: shared\nworkspaces:\n  - name: api\n    env:\n      MODE: api\n  - name: worker\n",
    )

    assert main(["--print", str(layout)]) == 0

    assert yaml.safe_load(capsys.readouterr().out) == {
        "workspaces": [
            {"name": "api", "env": {"MODE": "api"}},
            {"name": "worker", "env": {"MODE": "shared"}},
        ]
    }


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
