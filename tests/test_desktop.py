from pathlib import Path
from unittest.mock import Mock
import subprocess
import sys

import pytest

from lightacademia import desktop


def test_launcher_resolves_paths_and_stops_server(monkeypatch, tmp_path):
    process = Mock()
    process.poll.return_value = None
    popen = Mock(return_value=process)
    webview = Mock()
    monkeypatch.setitem(sys.modules, 'webview', webview)
    monkeypatch.setattr(desktop, 'set_macos_identity', lambda: None)
    monkeypatch.setattr(desktop.subprocess, 'Popen', popen)
    monkeypatch.setattr(desktop, 'wait_for_server', Mock())
    monkeypatch.chdir(tmp_path)
    assert desktop.main(['--notebook', 'notes', '--tools', 'tools', '--agent', 'claude']) == 0
    command = popen.call_args.args[0]
    assert command[command.index('--notebook') + 1] == str(tmp_path / 'notes')
    assert '--server.address=127.0.0.1' in command
    assert '--global.developmentMode=false' in command
    assert command[command.index('--agent') + 1] == 'claude'
    assert popen.call_args.kwargs['cwd'] == desktop.ROOT
    assert webview.create_window.call_args.args[0] == 'Light Academia'
    assert webview.create_window.call_args.kwargs['text_select'] is True
    assert Path(webview.start.call_args.kwargs['icon']).name == 'logo.png'
    process.terminate.assert_called_once()
    process.wait.assert_called_once()


def test_failed_startup_cleans_up(monkeypatch):
    process = Mock()
    process.poll.return_value = None
    monkeypatch.setitem(sys.modules, 'webview', Mock())
    monkeypatch.setattr(desktop, 'set_macos_identity', lambda: None)
    monkeypatch.setattr(desktop.subprocess, 'Popen', Mock(return_value=process))
    monkeypatch.setattr(desktop, 'wait_for_server', Mock(side_effect=RuntimeError('startup failed')))
    assert desktop.main([]) == 1
    process.terminate.assert_called_once()


def test_health_check_detects_child_exit():
    process = Mock()
    process.poll.return_value = 1
    process.returncode = 1
    with pytest.raises(RuntimeError, match='exited with status 1'):
        desktop.wait_for_server(process, 'http://127.0.0.1:1')


def test_shutdown_kills_unresponsive_server():
    process = Mock()
    process.poll.return_value = None
    process.wait.side_effect = [subprocess.TimeoutExpired('streamlit', 5), 0]
    desktop.stop_server(process)
    process.kill.assert_called_once()


def test_notebook_scripts_use_configured_python(monkeypatch):
    monkeypatch.setenv('LIGHTACADEMIA_PYTHON', '/research/venv/bin/python')
    assert desktop.script_python() == '/research/venv/bin/python'


def test_frozen_scripts_do_not_relaunch_desktop(monkeypatch):
    monkeypatch.delenv('LIGHTACADEMIA_PYTHON', raising=False)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(desktop.shutil, 'which', lambda name: '/usr/local/bin/python3')
    assert desktop.script_python() == '/usr/local/bin/python3'
