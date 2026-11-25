"""Run the Streamlit workspace in a native desktop window."""
from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

APP_NAME = "Light Academia"
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def script_python() -> str:
    """Choose the interpreter for notebook scripts, outside a frozen bundle."""
    configured = os.environ.get("LIGHTACADEMIA_PYTHON")
    if configured:
        return configured
    if not getattr(sys, "frozen", False):
        return sys.executable
    interpreter = shutil.which("python3") or shutil.which("python")
    if interpreter is None:
        raise RuntimeError("Install Python or set LIGHTACADEMIA_PYTHON to run notebook scripts.")
    return interpreter


def wait_for_server(process: subprocess.Popen, url: str, timeout: float = 60) -> None:
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Streamlit exited with status {process.returncode}. See its output above.")
        try:
            with opener.open(f"{url}/_stcore/health", timeout=1) as response:
                if response.status == 200 and response.read().strip() == b"ok":
                    return
        except (URLError, OSError):
            pass
        time.sleep(0.1)
    raise RuntimeError(f"Streamlit did not become ready within {timeout:g} seconds.")


def stop_server(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def set_macos_identity() -> None:
    if sys.platform == "darwin":
        # PyObjC exposes these native classes dynamically.
        from Foundation import NSBundle, NSProcessInfo  # type: ignore[attr-defined]

        NSProcessInfo.processInfo().setProcessName_(APP_NAME)
        info = NSBundle.mainBundle().infoDictionary()
        info["CFBundleName"] = APP_NAME
        info["CFBundleDisplayName"] = APP_NAME


def main(argv: list[str] | None = None) -> int:
    # Frozen executables re-enter here to run the server without opening another window.
    arguments = list(sys.argv[1:] if argv is None else argv)
    if getattr(sys, "frozen", False) and arguments[:1] == ["--_serve"]:
        from streamlit.web.cli import main as streamlit_main

        sys.argv = ["streamlit", *arguments[1:]]
        return streamlit_main()

    parser = argparse.ArgumentParser(description="Start Light Academia in a native desktop window.")
    parser.add_argument("--notebook", type=Path, default=Path.home() / "Light Academia" / "notebook")
    parser.add_argument("--tools", type=Path, default=Path.home() / "Light Academia" / "tools")
    parser.add_argument("--autocommit-seconds", type=int, default=300)
    parser.add_argument("--agent", choices=("codex", "claude"), default="codex")
    parser.add_argument("--codex-model")
    parser.add_argument("--agent-timeout-seconds", type=int, default=3600)
    parser.add_argument("--debug", action="store_true", help="Enable webview developer tools.")
    args = parser.parse_args(arguments)
    try:
        import webview

        set_macos_identity()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        command = ([sys.executable, "--_serve"] if getattr(sys, "frozen", False)
                   else [sys.executable, "-m", "streamlit"])
        command += ["run", str(ROOT / "app.py"), "--server.address=127.0.0.1",
                    f"--server.port={port}", "--global.developmentMode=false", "--server.headless=true", "--server.baseUrlPath=",
                    "--server.fileWatcherType=none", "--browser.gatherUsageStats=false", "--",
                    "--notebook", str(args.notebook.expanduser().resolve()),
                    "--tools", str(args.tools.expanduser().resolve()),
                    "--agent", args.agent, "--autocommit-seconds", str(args.autocommit_seconds),
                    "--agent-timeout-seconds", str(args.agent_timeout_seconds)]
        if args.codex_model:
            command += ["--codex-model", args.codex_model]
        process = subprocess.Popen(command, cwd=ROOT)
        try:
            wait_for_server(process, url)
            webview.create_window(APP_NAME, url, width=1440, height=960, min_size=(900, 600),
                                  text_select=True)
            webview.start(debug=args.debug, icon=str(ROOT / "assets" / "logo.png"))
        finally:
            stop_server(process)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"{APP_NAME}: {exc}", file=sys.stderr)
        return 1
    return 0
