from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, Protocol, runtime_checkable

from openai_codex import Codex, CodexConfig, Sandbox
from openai_codex.errors import CodexError
from openai_codex.models import Notification


class AgentError(RuntimeError):
    pass


class AgentStopped(AgentError):
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
StopCallback = Callable[[], bool]
DEFAULT_PROMPT_TEMPLATE = Path(__file__).resolve().parent.parent / "AGENT_PROMPT.md"


class Agent(Protocol):
    name: str

    def run(
        self,
        prompt: str,
        context: AgentContext,
        on_progress: ProgressCallback | None = None,
        should_stop: StopCallback | None = None,
    ) -> AgentResult:
        pass


@runtime_checkable
class ModelSelectableAgent(Protocol):
    """Optional capability for agents whose runnable models can be selected in the UI."""

    supports_model_selection: bool

    def available_models(self) -> list[str]:
        pass


class AgentSupport:
    """Shared prompt and temporary-tools support for local agent runtimes."""

    def __init__(self, *, prompt_template: Path | None = None) -> None:
        self.prompt_template = prompt_template or DEFAULT_PROMPT_TEMPLATE

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

    def _build_environment(self, tools_root: Path) -> dict[str, str]:
        environment = os.environ.copy()
        environment["LIGHTACADEMIA_TOOLS"] = str(tools_root.resolve())
        return environment

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


class CodexSdkAgent(AgentSupport):
    name = "Codex SDK"
    supports_model_selection = True

    def __init__(
        self,
        sandbox: str = "workspace-write",
        network_access: bool = True,
        timeout_seconds: int = 900,
        prompt_template: Path | None = None,
        model: str | None = None,
    ) -> None:
        super().__init__(prompt_template=prompt_template)
        self.sandbox = sandbox
        self.network_access = network_access
        self.timeout_seconds = timeout_seconds
        self.model = model.strip() if model and model.strip() else None

    def available_models(self) -> list[str]:
        """Return models available to the authenticated Codex SDK session."""
        try:
            with Codex() as codex:
                return [model.model for model in codex.models().data]
        except (CodexError, OSError, RuntimeError) as exc:
            raise AgentError(f"Could not list Codex models: {exc}") from exc

    def run(
        self,
        prompt: str,
        context: AgentContext,
        on_progress: ProgressCallback | None = None,
        should_stop: StopCallback | None = None,
    ) -> AgentResult:
        temporary = tempfile.TemporaryDirectory(prefix="lightacademia-codex-")
        tools_workspace = Path(temporary.name) / "tools"
        self._copy_tools_dir(context.tools_dir, tools_workspace)
        try:
            environment = self._build_environment(tools_workspace)
            config = CodexConfig(env=environment)
            sdk_sandbox = Sandbox(self.sandbox)
            thread_config = {
                "sandbox_workspace_write": {
                    "network_access": self.network_access,
                    "writable_roots": [str(tools_workspace.resolve())],
                }
            }
            with Codex(config) as codex:
                thread = codex.thread_start(
                    cwd=str(context.project_dir),
                    model=self.model,
                    sandbox=sdk_sandbox,
                    config=thread_config,
                )
                turn = thread.turn(self._build_prompt(prompt, context, tools_workspace))
                stop_watcher_done = threading.Event()
                timed_out = threading.Event()
                deadline = time.monotonic() + self.timeout_seconds

                def watch_for_stop() -> None:
                    while not stop_watcher_done.wait(0.1):
                        if should_stop is not None and should_stop():
                            turn.interrupt()
                            return
                        if time.monotonic() >= deadline:
                            timed_out.set()
                            turn.interrupt()
                            return

                watcher = threading.Thread(target=watch_for_stop, daemon=True)
                watcher.start()
                events: list[dict[str, Any]] = []
                tool_actions: list[str] = []
                final_response = ""
                try:
                    for notification in turn.stream():
                        event = codex_event_from_notification(notification)
                        events.append(event)
                        progress = codex_progress_from_event(event)
                        if progress is not None and on_progress is not None:
                            on_progress(progress)
                        tool_action = codex_tool_action_from_event(event)
                        if tool_action:
                            tool_actions.append(tool_action)
                        item = event.get("item")
                        if (
                            event.get("type") == "item.completed"
                            and isinstance(item, dict)
                            and item.get("type") == "agent_message"
                        ):
                            final_response = event_text(item)
                finally:
                    stop_watcher_done.set()
                    watcher.join(timeout=1)
                if timed_out.is_set():
                    raise AgentError(f"Codex SDK timed out after {self.timeout_seconds} seconds.")
                if should_stop is not None and should_stop():
                    raise AgentStopped("Agent run stopped by user.")
                return AgentResult(
                    response=final_response.strip(),
                    tool_actions=tool_actions,
                    raw_stdout="\n".join(json.dumps(event, ensure_ascii=False) for event in events),
                )
        except AgentError:
            raise
        except (CodexError, OSError, RuntimeError, ValueError) as exc:
            raise AgentError(f"Codex SDK failed: {exc}") from exc
        finally:
            temporary.cleanup()


class ClaudeCliAgent(AgentSupport):
    name = "Claude CLI"
    supports_model_selection = False

    def __init__(
        self,
        executable: str = "claude",
        permission_mode: str = "acceptEdits",
        timeout_seconds: int = 900,
        prompt_template: Path | None = None,
    ) -> None:
        super().__init__(prompt_template=prompt_template)
        self.executable = executable
        self.timeout_seconds = timeout_seconds
        self.permission_mode = permission_mode

    def run(
        self,
        prompt: str,
        context: AgentContext,
        on_progress: ProgressCallback | None = None,
        should_stop: StopCallback | None = None,
    ) -> AgentResult:
        executable = shutil.which(self.executable)
        if executable is None:
            raise AgentError(f"Could not find `{self.executable}` on PATH.")

        with tempfile.TemporaryDirectory(prefix="lightacademia-claude-") as tmpdir:
            tools_workspace = Path(tmpdir) / "tools"
            self._copy_tools_dir(context.tools_dir, tools_workspace)
            command = self._build_command(executable, context, tools_workspace)
            result = self._run_streaming(
                command,
                self._build_prompt(prompt, context, tools_workspace),
                on_progress,
                should_stop,
                cwd=context.project_dir,
                environment=self._build_environment(tools_workspace),
            )
            if result["returncode"] != 0:
                detail = result["stderr"].strip() or result["error"].strip() or "Claude CLI failed."
                raise AgentError(detail)
            return AgentResult(
                response=result["last_agent_message"].strip(),
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
    ) -> list[str]:
        return [
            executable,
            "--print",
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            self.permission_mode,
            "--add-dir",
            str(tools_workspace),
            "--allowedTools",
            "Read",
            "Write",
            "Edit",
            "MultiEdit",
            "Bash",
            "Glob",
            "Grep",
            "LS",
        ]

    def _run_streaming(
        self,
        command: list[str],
        prompt: str,
        on_progress: ProgressCallback | None,
        should_stop: StopCallback | None = None,
        cwd: Path | None = None,
        environment: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=cwd,
            env=environment,
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
                if should_stop is not None and should_stop():
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    raise AgentStopped("Agent run stopped by user.")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    process.kill()
                    process.wait()
                    raise AgentError(f"Claude CLI timed out after {self.timeout_seconds} seconds.")
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

                progress = claude_progress_from_event(event)
                if progress is not None and on_progress is not None:
                    on_progress(progress)
                tool_action = claude_tool_action_from_event(event)
                if tool_action:
                    tool_actions.append(tool_action)
                if event.get("type") == "error":
                    errors.append(event_text(event))
                message_text = claude_result_text_from_event(event)
                if not message_text and event.get("type") == "assistant":
                    message_text = claude_message_text(event.get("message"))
                if message_text:
                    last_agent_message = message_text

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                process.wait()
                raise AgentError(f"Claude CLI timed out after {self.timeout_seconds} seconds.")
            try:
                returncode = process.wait(timeout=remaining)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                process.wait()
                raise AgentError(
                    f"Claude CLI timed out after {self.timeout_seconds} seconds."
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


def create_agent(
    kind: str,
    timeout_seconds: int = 3600,
    codex_model: str | None = None,
) -> Agent:
    normalized = kind.strip().lower()
    if normalized == "codex":
        return CodexSdkAgent(timeout_seconds=timeout_seconds, model=codex_model)
    if normalized in {"claude", "claude-code", "claude_code"}:
        return ClaudeCliAgent(timeout_seconds=timeout_seconds)
    raise AgentError(f"Unknown agent: {kind}. Expected one of: codex, claude.")


def default_agent(
    kind: str = "codex",
    timeout_seconds: int = 3600,
    codex_model: str | None = None,
) -> Agent:
    return create_agent(kind, timeout_seconds=timeout_seconds, codex_model=codex_model)


def available_agent_models(
    kind: str,
    *,
    codex_model: str | None = None,
) -> list[str] | None:
    """Return selectable models when an agent implements that optional capability."""
    agent = create_agent(kind, codex_model=codex_model)
    if not isinstance(agent, ModelSelectableAgent) or not agent.supports_model_selection:
        return None
    return agent.available_models()


def codex_event_from_notification(notification: Notification) -> dict[str, Any]:
    """Translate typed SDK notifications into the UI's stable event shape."""
    event_type = notification.method.replace("/", ".")
    payload = notification.payload
    if hasattr(payload, "model_dump"):
        data = payload.model_dump(mode="json", by_alias=False)
    else:
        data = {"detail": str(payload)}
    event: dict[str, Any] = {"type": event_type, **data}
    item = event.get("item")
    if isinstance(item, dict):
        item_type = item.get("type")
        item["type"] = {
            "agentMessage": "agent_message",
            "commandExecution": "command_execution",
            "fileChange": "file_change",
            "mcpToolCall": "mcp_tool_call",
            "webSearch": "web_search",
        }.get(item_type, item_type)
        if item.get("type") == "reasoning":
            item["text"] = "\n".join(item.get("summary") or item.get("content") or [])
    if event_type in {"turn.started", "turn.completed"}:
        turn = event.get("turn")
        if isinstance(turn, dict):
            event["usage"] = turn.get("usage")
    return event


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


def claude_progress_from_event(event: dict[str, Any]) -> AgentProgress | None:
    event_type = str(event.get("type") or "event")
    if event_type == "system":
        subtype = str(event.get("subtype") or "system")
        return AgentProgress(event_type, f"System: {subtype}")
    if event_type == "assistant":
        text = claude_message_text(event.get("message"))
        return AgentProgress(event_type, text or _pretty_event(event))
    if event_type == "result":
        detail = claude_result_text_from_event(event) or _pretty_event(event)
        return AgentProgress(event_type, detail)
    if event_type == "error":
        return AgentProgress(event_type, event_text(event) or _pretty_event(event))
    return AgentProgress(event_type, event_text(event) or _pretty_event(event))


def claude_tool_action_from_event(event: dict[str, Any]) -> str | None:
    if event.get("type") != "assistant":
        return None
    message = event.get("message")
    if not isinstance(message, dict):
        return None
    for block in message.get("content") or []:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = str(block.get("name") or "tool")
        tool_input = block.get("input")
        if name == "Bash" and isinstance(tool_input, dict) and tool_input.get("command"):
            return str(tool_input["command"])
        return name
    return None


def claude_result_text_from_event(event: dict[str, Any]) -> str:
    if event.get("type") != "result":
        return ""
    result = event.get("result")
    if isinstance(result, str):
        return result
    return event_text(result)


def claude_message_text(message: Any) -> str:
    if not isinstance(message, dict):
        return event_text(message)
    parts = []
    for block in message.get("content") or []:
        if not isinstance(block, dict):
            text = event_text(block)
            if text:
                parts.append(text)
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = event_text(block)
            if text:
                parts.append(text)
        elif block_type == "tool_use":
            name = str(block.get("name") or "tool")
            tool_input = block.get("input")
            if name == "Bash" and isinstance(tool_input, dict) and tool_input.get("command"):
                parts.append(f"$ {tool_input['command']}")
            else:
                parts.append(f"{name}: {_pretty_value(tool_input)}")
    return "\n".join(parts)


def _pretty_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


def _pretty_event(event: dict[str, Any]) -> str:
    visible = {key: value for key, value in event.items() if key != "encrypted_content"}
    return json.dumps(visible, indent=2, ensure_ascii=False, default=str)
