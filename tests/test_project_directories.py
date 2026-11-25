from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler
from unittest.mock import Mock

import pytest
import yaml

from lightacademia.storage import Project, project_local_directories, save_project_local_directories
from lightacademia.agents import AgentContext, CodexSdkAgent, ClaudeCliAgent
from lightacademia.directory_picker import directory_picker_bridge, pick_local_directory


def test_paths_are_home_relative_and_metadata_is_preserved(tmp_path, monkeypatch):
    project = Project("project", tmp_path)
    (tmp_path / "meta.yaml").write_text("title: Example\ntags: [code]\n")
    save_project_local_directories(project, [str(Path.home() / "code"), "~/code", "/external/code"])
    assert project_local_directories(project) == ["~/code", "/external/code"]
    assert yaml.safe_load((tmp_path / "meta.yaml").read_text())["tags"] == ["code"]
    context = AgentContext(tmp_path, "project", tmp_path / "tools")
    agent = CodexSdkAgent()
    assert str(Path.home() / "code") in agent._writable_roots(context, tmp_path / "tools")
    assert str(Path.home() / "code") in agent._build_prompt("hello", context)
    command = ClaudeCliAgent()._build_command("claude", context, tmp_path / "tools")
    assert str(Path.home() / "code") in command
    save_project_local_directories(project, [])
    assert project_local_directories(project) == []
    assert str(Path.home() / "code") not in agent._writable_roots(context, tmp_path / "tools")


@pytest.mark.parametrize("selected", [None, "/code/repository"])
def test_picker_roundtrip_and_authentication(monkeypatch, selected):
    pick = Mock(return_value=selected)
    with directory_picker_bridge(pick) as environment:
        for key, value in environment.items():
            monkeypatch.setenv(key, value)
        assert pick_local_directory() == selected
        request = Request(environment["LIGHTACADEMIA_PICKER_URL"], data=b"", method="POST")
        with pytest.raises(HTTPError) as error:
            build_opener(ProxyHandler({})).open(request)
        assert error.value.code == 403
        pick.assert_called_once()


def test_picker_without_desktop(monkeypatch):
    monkeypatch.delenv("LIGHTACADEMIA_PICKER_URL", raising=False)
    with pytest.raises(OSError, match="desktop"):
        pick_local_directory()


def test_directory_controls_in_settings(tmp_path):
    from streamlit.testing.v1 import AppTest
    project = Project("project", tmp_path)
    save_project_local_directories(project, ["/external/code"])
    (tmp_path / "Home.md").write_text("# Home")
    script = """
from pathlib import Path
from app import settings_dialog
from lightacademia.storage import Project, Note
settings_dialog(Project('project', Path(ROOT)), Note('Home.md', Path(ROOT) / 'Home.md'))
""".replace("ROOT", repr(str(tmp_path)))
    at = AppTest.from_string(script).run()
    assert not at.exception
    assert any(code.value == "/external/code" for code in at.code)
    assert any(button.label == "Add directory" for button in at.button)
    assert any(button.label == "Remove" for button in at.button)
