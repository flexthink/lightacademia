from __future__ import annotations

import json
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, Protocol


class AgentError(RuntimeError):
    pass


@dataclass(frozen=True)
class AgentContext:
    project_dir: Path
    project_name: str
    tools_dir: Path
    current_note: str | None = None


@dataclass(frozen=True)
class AgentResult:
    response: str
    tool_actions: list[str] = field(default_factory=list)
    file_changes: list[str] = field(default_factory=list)
    raw_stdout: str = ""
    raw_stderr: str = ""
    returncode: int = 0


@dataclass(frozen=True)
class AgentProgress:
    event_type: str
    text: str


ProgressCallback = Callable[[AgentProgress], None]
DEFAULT_PROMPT_TEMPLATE = Path(__file__).resolve().parent.parent / "AGENT_PROMPT.md"


class Agent(Protocol):
    name: str

    def run(
        self,
        prompt: str,
        context: AgentContext,
        on_progress: ProgressCallback | None = None,
    ) -> AgentResult:
        pass


class CodexCliAgent:
    name = "Codex CLI"

    def __init__(
        self,
        executable: str = "codex",
        sandbox: str = "workspace-write",
        network_access: bool = True,
        timeout_seconds: int = 900,
        prompt_template: Path | None = None,
    ) -> None:
        self.executable = executable
        self.sandbox = sandbox
        self.network_access = network_access
        self.timeout_seconds = timeout_seconds
        self.prompt_template = prompt_template or DEFAULT_PROMPT_TEMPLATE

    def run(
        self,
        prompt: str,
        context: AgentContext,
        on_progress: ProgressCallback | None = None,
    ) -> AgentResult:
        executable = shutil.which(self.executable)
        if executable is None:
            raise AgentError(f"Could not find `{self.executable}` on PATH.")

        with tempfile.TemporaryDirectory(prefix="lightacademia-codex-") as tmpdir:
            tools_workspace = Path(tmpdir) / "tools"
            self._copy_tools_dir(context.tools_dir, tools_workspace)
            output_path = Path(tmpdir) / "last-message.md"
            command = self._build_command(executable, context, tools_workspace, output_path)
            result = self._run_streaming(
                command,
                self._build_prompt(prompt, context, tools_workspace),
                on_progress,
            )
            response = output_path.read_text(encoding="utf-8") if output_path.exists() else ""
            if result["returncode"] != 0:
                detail = result["stderr"].strip() or result["error"].strip() or "Codex CLI failed."
                raise AgentError(detail)
            return AgentResult(
                response=response.strip() or result["last_agent_message"].strip(),
                tool_actions=result["tool_actions"],
                raw_stdout=result["stdout"],
                raw_stderr=result["stderr"],
                returncode=result["returncode"],
            )

    def _build_command(
        self,
        executable: str,
        context: AgentContext,
        tools_workspace: Path,
        output_path: Path,
    ) -> list[str]:
        command = [
            executable,
            "exec",
            "--cd",
            str(context.project_dir),
            "--add-dir",
            str(tools_workspace),
            "--sandbox",
            self.sandbox,
        ]
        if self.sandbox == "workspace-write":
            command.extend(
                [
                    "--config",
                    f"sandbox_workspace_write.network_access={str(self.network_access).lower()}",
                ]
            )
        command.extend(
            [
                "--json",
                "--output-last-message",
                str(output_path),
                "-",
            ]
        )
        return command

    def _run_streaming(
        self,
        command: list[str],
        prompt: str,
        on_progress: ProgressCallback | None,
    ) -> dict[str, Any]:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None

        output_queue: queue.Queue[tuple[str, str | None]] = queue.Queue()
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        tool_actions: list[str] = []
        errors: list[str] = []
        last_agent_message = ""

        def pump_stream(name: str, stream: IO[str]) -> None:
            try:
                for line in stream:
                    output_queue.put((name, line))
            finally:
                output_queue.put((name, None))

        readers = [
            threading.Thread(target=pump_stream, args=("stdout", process.stdout), daemon=True),
            threading.Thread(target=pump_stream, args=("stderr", process.stderr), daemon=True),
        ]
        for reader in readers:
            reader.start()

        try:
            try:
                process.stdin.write(prompt)
                process.stdin.close()
            except BrokenPipeError:
                pass

            deadline = time.monotonic() + self.timeout_seconds
            closed_streams: set[str] = set()
            while len(closed_streams) < 2:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    process.kill()
                    process.wait()
                    raise AgentError(f"Codex CLI timed out after {self.timeout_seconds} seconds.")
                try:
                    source, line = output_queue.get(timeout=min(0.25, remaining))
                except queue.Empty:
                    continue
                if line is None:
                    closed_streams.add(source)
                    continue
                if source == "stderr":
                    stderr_lines.append(line)
                    continue

                stdout_lines.append(line)
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                progress = codex_progress_from_event(event)
                if progress is not None and on_progress is not None:
                    on_progress(progress)
                tool_action = codex_tool_action_from_event(event)
                if tool_action:
                    tool_actions.append(tool_action)
                if event.get("type") == "error":
                    errors.append(event_text(event))
                item = event.get("item")
                if (
                    event.get("type") == "item.completed"
                    and isinstance(item, dict)
                    and item.get("type") == "agent_message"
                ):
                    last_agent_message = event_text(item)

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                process.wait()
                raise AgentError(f"Codex CLI timed out after {self.timeout_seconds} seconds.")
            try:
                returncode = process.wait(timeout=remaining)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                process.wait()
                raise AgentError(
                    f"Codex CLI timed out after {self.timeout_seconds} seconds."
                ) from exc
        except BaseException:
            if process.poll() is None:
                process.kill()
                process.wait()
            raise
        finally:
            for reader in readers:
                reader.join(timeout=1)
            process.stdout.close()
            process.stderr.close()

        return {
            "returncode": returncode,
            "stdout": "".join(stdout_lines),
            "stderr": "".join(stderr_lines),
            "tool_actions": tool_actions,
            "error": "\n".join(error for error in errors if error),
            "last_agent_message": last_agent_message,
        }

    def _build_prompt(self, prompt: str, context: AgentContext, tools_workspace: Path | None = None) -> str:
        current_note = context.current_note or "none"
        tools_root = tools_workspace or context.tools_dir
        try:
            template = self.prompt_template.read_text(encoding="utf-8")
        except OSError as exc:
            raise AgentError(f"Could not read agent prompt template: {self.prompt_template}") from exc

        replacements = {
            "{{project_name}}": context.project_name,
            "{{project_dir}}": str(context.project_dir),
            "{{tools_dir}}": str(context.tools_dir),
            "{{tools_root}}": str(tools_root),
            "{{current_note}}": current_note,
            "{{user_prompt}}": prompt,
        }
        for placeholder, value in replacements.items():
            template = template.replace(placeholder, value)
        return template

    def _copy_tools_dir(self, source: Path, destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        if not source.exists():
            return
        for child in source.iterdir():
            if child.is_symlink():
                continue
            target = destination / child.name
            if child.is_dir():
                shutil.copytree(child, target, ignore=self._ignore_symlinks)
            elif child.is_file():
                shutil.copy2(child, target)

    def _ignore_symlinks(self, directory: str, names: list[str]) -> list[str]:
        return [name for name in names if (Path(directory) / name).is_symlink()]


def default_agent() -> Agent:
    return CodexCliAgent()


def event_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(filter(None, (event_text(item) for item in value)))
    if not isinstance(value, dict):
        return "" if value is None else str(value)
    for key in ("text", "message", "summary", "content"):
        text = event_text(value.get(key))
        if text:
            return text
    return ""


def codex_progress_from_event(event: dict[str, Any]) -> AgentProgress | None:
    event_type = str(event.get("type") or "event")
    if event_type == "thread.started":
        return AgentProgress(event_type, f"Session started: {event.get('thread_id', 'unknown')}")
    if event_type == "turn.started":
        return AgentProgress(event_type, "Turn started")
    if event_type == "turn.completed":
        usage = event.get("usage")
        detail = json.dumps(usage, indent=2, ensure_ascii=False) if usage else "Turn completed"
        return AgentProgress(event_type, detail)
    if event_type in {"turn.failed", "error"}:
        return AgentProgress(event_type, event_text(event) or _pretty_event(event))

    item = event.get("item")
    if not event_type.startswith("item.") or not isinstance(item, dict):
        return None

    item_type = str(item.get("type") or "item")
    phase = event_type.removeprefix("item.")
    detail = _codex_item_detail(item)
    heading = f"{item_type} {phase}"
    return AgentProgress(event_type, f"{heading}\n{detail}" if detail else heading)


def _codex_item_detail(item: dict[str, Any]) -> str:
    item_type = item.get("type")
    if item_type in {"agent_message", "reasoning"}:
        return event_text(item)
    if item_type == "command_execution":
        parts = []
        command = item.get("command")
        if command:
            parts.append(f"$ {command}")
        output = item.get("aggregated_output") or item.get("output")
        if output:
            parts.append(event_text(output) or str(output))
        if item.get("exit_code") is not None:
            parts.append(f"exit code: {item['exit_code']}")
        return "\n".join(parts)
    if item_type == "mcp_tool_call":
        parts = [
            ".".join(
                str(value)
                for value in (item.get("server"), item.get("tool"))
                if value
            )
        ]
        for key in ("arguments", "result", "error"):
            if item.get(key) is not None:
                parts.append(f"{key}: {_pretty_value(item[key])}")
        return "\n".join(filter(None, parts))
    if item_type == "web_search":
        return event_text(item.get("query")) or _pretty_event(item)
    if item_type in {"file_change", "plan_update", "todo_list"}:
        return _pretty_event(item)
    return event_text(item) or _pretty_event(item)


def codex_tool_action_from_event(event: dict[str, Any]) -> str | None:
    if event.get("type") != "item.started":
        return None
    item = event.get("item")
    if not isinstance(item, dict):
        return None
    if item.get("type") == "command_execution" and item.get("command"):
        return str(item["command"])
    if item.get("type") == "mcp_tool_call":
        name = ".".join(
            str(value) for value in (item.get("server"), item.get("tool")) if value
        )
        return f"MCP: {name}" if name else "MCP tool call"
    return None


def _pretty_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


def _pretty_event(event: dict[str, Any]) -> str:
    visible = {key: value for key, value in event.items() if key != "encrypted_content"}
    return json.dumps(visible, indent=2, ensure_ascii=False, default=str)
