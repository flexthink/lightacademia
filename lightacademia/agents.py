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
from typing import TYPE_CHECKING, IO, Any, Protocol, runtime_checkable

from lightacademia.agent_progress import ActivityUpdate, AgentProgress

if TYPE_CHECKING:
    from openai_codex.models import Notification

try:
    from openai_codex import Codex, CodexConfig, Sandbox
    from openai_codex.errors import CodexError
except ImportError:
    Codex = None  # type: ignore[assignment]
    CodexConfig = None  # type: ignore[assignment]
    Sandbox = None  # type: ignore[assignment]

    class CodexError(Exception):
        """Fallback used so non-Codex agents work without the optional SDK."""


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


ProgressCallback = Callable[[AgentProgress], None]
StopCallback = Callable[[], bool]
DEFAULT_PROMPT_TEMPLATE = Path(__file__).resolve().parent.parent / "AGENT_PROMPT.md"
ACTION_PROMPT_TEMPLATE = Path(__file__).resolve().parent.parent / "AGENT_PROMPT_ACTION.md"
CHAT_PROMPT_TEMPLATE = Path(__file__).resolve().parent.parent / "AGENT_PROMPT_CHAT.md"


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


@dataclass(frozen=True)
class AgentChatResult:
    """One asynchronous chat turn and the opaque state for its next turn."""

    response: str
    details: dict[str, Any] = field(default_factory=dict)
    tool_actions: list[str] = field(default_factory=list)
    raw_stdout: str = ""


@runtime_checkable
class ChatCapableAgent(Protocol):
    """Optional capability for agents that retain context across chat turns."""

    supports_chat: bool

    def chat(
        self,
        prompt: str,
        context: AgentContext,
        details: dict[str, Any] | None = None,
        on_progress: ProgressCallback | None = None,
        should_stop: StopCallback | None = None,
    ) -> AgentChatResult:
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

    def _build_prompt(
        self,
        prompt: str,
        context: AgentContext,
        tools_workspace: Path | None = None,
        *,
        prompt_template: Path | None = None,
    ) -> str:
        current_note = context.current_note or "none"
        tools_root = tools_workspace or context.tools_dir
        try:
            template = self.prompt_template.read_text(encoding="utf-8")
            if prompt_template is not None:
                context_instructions = prompt_template.read_text(encoding="utf-8").strip()
                template = template.replace("{{context_specific_instructions}}", context_instructions)
        except OSError as exc:
            raise AgentError(f"Could not read agent prompt template: {prompt_template or self.prompt_template}") from exc

        replacements = {
            "{{project_name}}": context.project_name,
            "{{project_dir}}": str(context.project_dir),
            "{{tools_dir}}": str(context.tools_dir),
            "{{tools_root}}": str(tools_root),
            "{{current_note}}": current_note,
            "{{user_prompt}}": prompt,
            "{{context_specific_instructions}}": "",
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
    supports_chat = True

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
        """Return selectable models with the SDK default first."""
        if Codex is None:
            raise _missing_codex_sdk_error()
        try:
            with Codex() as codex:
                models = codex.models().data
                model_names = [
                    model.model
                    for model in [
                        *(model for model in models if model.is_default),
                        *(model for model in models if not model.is_default),
                    ]
                ]
                if "gpt-reserve" not in model_names:
                    model_names.append("gpt-reserve")
                return list(dict.fromkeys(model_names))
        except (CodexError, OSError, RuntimeError) as exc:
            raise AgentError(f"Could not list Codex models: {exc}") from exc

    def run(
        self,
        prompt: str,
        context: AgentContext,
        on_progress: ProgressCallback | None = None,
        should_stop: StopCallback | None = None,
    ) -> AgentResult:
        if Codex is None or CodexConfig is None or Sandbox is None:
            raise _missing_codex_sdk_error()
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
                turn = thread.turn(self._build_prompt(
                    prompt, context, tools_workspace, prompt_template=ACTION_PROMPT_TEMPLATE
                ))
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

    def chat(
        self,
        prompt: str,
        context: AgentContext,
        details: dict[str, Any] | None = None,
        on_progress: ProgressCallback | None = None,
        should_stop: StopCallback | None = None,
    ) -> AgentChatResult:
        """Run a turn in a Codex thread, returning its ID for subsequent turns.

        The caller owns the returned details and can persist them between async
        calls. Only the thread ID is needed to continue a Codex conversation.
        """
        if Codex is None or CodexConfig is None or Sandbox is None:
            raise _missing_codex_sdk_error()
        details = dict(details or {})
        thread_id = details.get("thread_id")
        temporary = tempfile.TemporaryDirectory(prefix="lightacademia-codex-chat-")
        tools_workspace = Path(temporary.name) / "tools"
        self._copy_tools_dir(context.tools_dir, tools_workspace)
        try:
            config = CodexConfig(env=self._build_environment(tools_workspace))
            thread_config = {
                "sandbox_workspace_write": {
                    "network_access": self.network_access,
                    "writable_roots": [str(tools_workspace.resolve())],
                }
            }
            with Codex(config) as codex:
                if thread_id:
                    thread = codex.thread_resume(
                        str(thread_id), cwd=str(context.project_dir), model=self.model,
                        sandbox=Sandbox(self.sandbox), config=thread_config,
                    )
                else:
                    thread = codex.thread_start(
                        cwd=str(context.project_dir), model=self.model,
                        sandbox=Sandbox(self.sandbox), config=thread_config,
                    )
                turn = thread.turn(self._build_prompt(
                    prompt, context, tools_workspace, prompt_template=CHAT_PROMPT_TEMPLATE
                ))
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
                actions: list[str] = []
                response = ""
                try:
                    for notification in turn.stream():
                        event = codex_event_from_notification(notification)
                        events.append(event)
                        progress = codex_progress_from_event(event)
                        if progress is not None and on_progress is not None:
                            on_progress(progress)
                        action = codex_tool_action_from_event(event)
                        if action:
                            actions.append(action)
                        item = event.get("item")
                        if (event.get("type") == "item.completed" and isinstance(item, dict)
                                and item.get("type") == "agent_message"):
                            response = event_text(item)
                finally:
                    stop_watcher_done.set()
                    watcher.join(timeout=1)
                if timed_out.is_set():
                    raise AgentError(f"Codex SDK timed out after {self.timeout_seconds} seconds.")
                if should_stop is not None and should_stop():
                    raise AgentStopped("Agent chat stopped by user.")
                next_details = {**details, "thread_id": str(thread.id)}
                return AgentChatResult(
                    response=response.strip(), details=next_details, tool_actions=actions,
                    raw_stdout="\n".join(json.dumps(event, ensure_ascii=False) for event in events),
                )
        except AgentError:
            raise
        except (CodexError, OSError, RuntimeError, ValueError) as exc:
            raise AgentError(f"Codex SDK chat failed: {exc}") from exc
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


def _missing_codex_sdk_error() -> AgentError:
    return AgentError(
        "The Codex agent requires the optional `openai-codex` package. "
        "Install it with `pip install -r requirements-codex.txt`."
    )


def codex_event_from_notification(notification: "Notification") -> dict[str, Any]:
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
            item["text"] = event_text(item.get("summary") or item.get("content"))
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
    """Adapt SDK snapshots/deltas to the provider-independent activity contract."""
    event_type = str(event.get("type") or "event")
    lifecycle = {"thread.started", "turn.started", "turn.completed", "turn.failed", "error"}
    if event_type in lifecycle:
        failed = event_type in {"turn.failed", "error"}
        turn = event.get("turn")
        if isinstance(turn, dict) and turn.get("status") in {"failed", "interrupted"}:
            failed = True
        state = "error" if failed else "complete" if event_type == "turn.completed" else "running"
        detail = event_text(event.get("error")) or event_text(turn.get("error") if isinstance(turn, dict) else None) or event_text(event)
        update = ActivityUpdate(None, "error" if failed else "lifecycle", "Error" if failed else "Robot activity", state, {"text": detail} if detail else {})
        return AgentProgress(event_type, detail or event_type, update)

    delta_types = {
        "item.agentMessage.delta": ("message", "Reply", "text"),
        "item.reasoning.summaryTextDelta": ("reasoning", "Thinking", f"summary:{event.get('summary_index', 0):04d}"),
        "item.reasoning.textDelta": ("reasoning", "Thinking", f"content:{event.get('content_index', 0):04d}"),
        "item.commandExecution.outputDelta": ("command", "Command", "output"),
        "item.fileChange.outputDelta": ("files", "File changes", "output"),
        "item.plan.delta": ("plan", "Plan", "text"),
    }
    if event_type in delta_types:
        kind, label, section = delta_types[event_type]
        text = event_text(event.get("delta"))
        return AgentProgress(event_type, text, ActivityUpdate(event.get("item_id"), kind, label, content={section: text}, append=True))

    if event_type == "item.mcpToolCall.progress":
        text = event_text(event.get("message"))
        return AgentProgress(event_type, text, ActivityUpdate(event.get("item_id"), "tool", "Tool call", content={"progress": text + "\n"}, append=True))

    item = event.get("item")
    if event_type not in {"item.started", "item.updated", "item.completed"} or not isinstance(item, dict):
        return None
    item_type = str(item.get("type") or "item")
    phase = event_type.removeprefix("item.")
    failed = item.get("status") in {"failed", "declined"} or bool(item.get("error")) or item.get("exit_code") not in {None, 0}
    state = "error" if failed else "complete" if phase == "completed" else "running"
    kinds = {
        "agent_message": ("message", "Reply"), "reasoning": ("reasoning", "Thinking"),
        "command_execution": ("command", "Command"), "file_change": ("files", "File changes"),
        "mcp_tool_call": ("tool", ".".join(str(item[k]) for k in ("server", "tool") if item.get(k)) or "Tool call"),
        "web_search": ("search", "Web search"), "plan": ("plan", "Plan"),
        "plan_update": ("plan", "Plan"), "todo_list": ("plan", "Plan"),
    }
    kind, label = kinds.get(item_type, ("log", "Activity"))
    content = {}
    if kind == "reasoning":
        for channel in ("summary", "content"):
            for index, part in enumerate(item.get(channel) or []):
                content[f"{channel}:{index:04d}"] = event_text(part)
        if not any(content.values()) and item.get("text"):
            content["summary:0000"] = event_text(item["text"])
    elif kind == "command":
        if item.get("command"):
            content["command"] = f"$ {item['command']}"
        output = item.get("aggregated_output") or item.get("output")
        if output:
            content["output"] = event_text(output)
        if item.get("exit_code") is not None:
            content["exit_code"] = f"Exit code: {item['exit_code']}"
    elif kind == "files":
        for index, change in enumerate(item.get("changes") or []):
            if isinstance(change, dict):
                content[f"file:{index}"] = "\n".join(str(change[k]) for k in ("path", "diff") if change.get(k))
    else:
        content["text"] = _codex_item_detail(item)
    detail = _codex_item_detail(item)
    heading = f"{item_type} {phase}"
    update = ActivityUpdate(item.get("id"), kind, label, state, content)
    return AgentProgress(event_type, f"{heading}\n{detail}" if detail else heading, update)


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
