from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from lightacademia.agents import (
    AgentContext,
    AgentError,
    AgentProgress,
    AgentStopped,
    ClaudeCliAgent,
    CodexSdkAgent,
    available_agent_models,
    create_agent,
    claude_progress_from_event,
    claude_tool_action_from_event,
    codex_event_from_notification,
    codex_progress_from_event,
    codex_tool_action_from_event,
)


class CodexProgressTest(unittest.TestCase):
    @patch("lightacademia.agents.Codex", None)
    def test_codex_dependency_is_optional_until_agent_is_used(self) -> None:
        agent = CodexSdkAgent()

        with self.assertRaisesRegex(AgentError, "requirements-codex.txt"):
            agent.available_models()

    @patch("lightacademia.agents.Codex")
    def test_codex_lists_models_with_sdk(self, codex_class) -> None:
        codex = codex_class.return_value.__enter__.return_value
        codex.models.return_value.data = [
            SimpleNamespace(model="gpt-5-codex"),
            SimpleNamespace(model="gpt-4.1"),
        ]

        self.assertEqual(CodexSdkAgent().available_models(), ["gpt-5-codex", "gpt-4.1"])
        codex.models.assert_called_once_with()

    def test_agents_without_model_capability_do_not_list_models(self) -> None:
        self.assertIsNone(available_agent_models("claude"))

    def test_builds_prompt_from_external_template(self) -> None:
        agent = CodexSdkAgent()
        context = AgentContext(
            project_dir=Path("/project"),
            project_name="Research",
            tools_dir=Path("/tools"),
            current_note="Results.md",
        )

        prompt = agent._build_prompt("Summarize the results.", context)

        self.assertIn("Project: Research", prompt)
        self.assertIn("Current note: Results.md", prompt)
        self.assertIn("User request:\nSummarize the results.", prompt)
        self.assertNotIn("{{project_name}}", prompt)
        self.assertNotIn("{{user_prompt}}", prompt)

    def test_builds_runtime_tools_environment(self) -> None:
        agent = CodexSdkAgent()

        environment = agent._build_environment(Path("/temporary/tools"))

        self.assertEqual(environment["LIGHTACADEMIA_TOOLS"], "/temporary/tools")

    def test_prompt_requires_runtime_tools_environment(self) -> None:
        agent = CodexSdkAgent()
        context = AgentContext(
            project_dir=Path("/project"),
            project_name="project",
            tools_dir=Path("/tools"),
            current_note="Home.md",
        )

        prompt = agent._build_prompt("Refresh the board.", context, Path("/temporary/tools"))

        self.assertIn("LIGHTACADEMIA_TOOLS=/temporary/tools", prompt)
        self.assertIn("Never hardcode or save the current available tools root", prompt)

    def test_claude_command_uses_stream_json_print_mode(self) -> None:
        agent = ClaudeCliAgent()
        context = AgentContext(
            project_dir=Path("/project"),
            project_name="project",
            tools_dir=Path("/tools"),
            current_note="Home.md",
        )

        command = agent._build_command("claude", context, Path("/temporary/tools"))

        self.assertEqual(command[:5], ["claude", "--print", "--output-format", "stream-json", "--verbose"])
        self.assertIn("--permission-mode", command)
        self.assertIn("acceptEdits", command)
        self.assertIn("--add-dir", command)
        self.assertIn("/temporary/tools", command)
        self.assertIn("--allowedTools", command)
        self.assertIn("Bash", command)
        self.assertNotIn("-", command)

    def test_create_agent_selects_implementation(self) -> None:
        self.assertIsInstance(create_agent("codex"), CodexSdkAgent)
        self.assertIsInstance(create_agent("claude"), ClaudeCliAgent)

    def test_create_agent_applies_timeout(self) -> None:
        codex = create_agent("codex", timeout_seconds=1234)
        claude = create_agent("claude", timeout_seconds=1234)

        self.assertEqual(codex.timeout_seconds, 1234)
        self.assertEqual(claude.timeout_seconds, 1234)

    def test_preserves_reasoning_text(self) -> None:
        progress = codex_progress_from_event(
            {
                "type": "item.completed",
                "item": {
                    "id": "item_1",
                    "type": "reasoning",
                    "text": "I will inspect the metrics before plotting them.",
                },
            }
        )

        self.assertIsNotNone(progress)
        assert progress is not None
        self.assertIn("reasoning completed", progress.text)
        self.assertIn("inspect the metrics", progress.text)

    def test_includes_command_and_output(self) -> None:
        event = {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": "python code/plot.py",
                "aggregated_output": "wrote assets/plot.png",
                "exit_code": 0,
            },
        }

        progress = codex_progress_from_event(event)

        self.assertIsNotNone(progress)
        assert progress is not None
        self.assertIn("$ python code/plot.py", progress.text)
        self.assertIn("wrote assets/plot.png", progress.text)
        self.assertIn("exit code: 0", progress.text)

    def test_extracts_tool_action_from_started_command(self) -> None:
        action = codex_tool_action_from_event(
            {
                "type": "item.started",
                "item": {"type": "command_execution", "command": "ls data"},
            }
        )

        self.assertEqual(action, "ls data")

    def test_extracts_claude_progress_and_tool_action(self) -> None:
        event = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Checking files."},
                    {"type": "tool_use", "name": "Bash", "input": {"command": "ls data"}},
                ]
            },
        }

        progress = claude_progress_from_event(event)
        action = claude_tool_action_from_event(event)

        self.assertIsNotNone(progress)
        assert progress is not None
        self.assertIn("Checking files.", progress.text)
        self.assertIn("$ ls data", progress.text)
        self.assertEqual(action, "ls data")

    def test_translates_typed_sdk_notification(self) -> None:
        payload = MagicMock()
        payload.model_dump.return_value = {
            "item": {"type": "commandExecution", "command": "ls data"}
        }
        event = codex_event_from_notification(
            SimpleNamespace(method="item/started", payload=payload)
        )

        self.assertEqual(event["type"], "item.started")
        self.assertEqual(event["item"]["type"], "command_execution")
        self.assertEqual(codex_tool_action_from_event(event), "ls data")

    @patch("lightacademia.agents.Codex")
    def test_codex_runs_through_sdk_thread(self, codex_class) -> None:
        codex = codex_class.return_value.__enter__.return_value
        thread = codex.thread_start.return_value
        turn = thread.turn.return_value
        payload = MagicMock()
        payload.model_dump.return_value = {
            "item": {"type": "agentMessage", "text": "Done."}
        }
        turn.stream.return_value = [
            SimpleNamespace(method="item/completed", payload=payload)
        ]
        context = AgentContext(
            project_dir=Path("/project"),
            project_name="project",
            tools_dir=Path("/missing-tools"),
            current_note="Home.md",
        )

        result = CodexSdkAgent(model="gpt-test").run("Finish it.", context)

        self.assertEqual(result.response, "Done.")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(codex.thread_start.call_args.kwargs["cwd"], "/project")
        self.assertEqual(codex.thread_start.call_args.kwargs["model"], "gpt-test")
        self.assertIn("Finish it.", thread.turn.call_args.args[0])

    def test_streams_claude_jsonl_subprocess_without_model_call(self) -> None:
        script = """
import json
import os
import sys

sys.stdin.read()
events = [
    {"type": "system", "subtype": "init"},
    {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Checking files."},
        {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
    ]}},
    {"type": "result", "result": os.environ["LIGHTACADEMIA_TOOLS"]},
]
for event in events:
    print(json.dumps(event), flush=True)
print("diagnostic", file=sys.stderr, flush=True)
"""
        progress: list[AgentProgress] = []
        agent = ClaudeCliAgent(timeout_seconds=5)

        result = agent._run_streaming(
            [sys.executable, "-u", "-c", script],
            "test prompt",
            progress.append,
            environment=agent._build_environment(Path("/temporary/tools")),
        )

        self.assertEqual(result["returncode"], 0)
        self.assertEqual(result["last_agent_message"], "/temporary/tools")
        self.assertEqual(result["tool_actions"], ["ls"])
        self.assertIn("diagnostic", result["stderr"])
        self.assertTrue(any("Checking files" in item.text for item in progress))

    def test_stop_callback_terminates_streaming_subprocess(self) -> None:
        script = """
import json
import sys
import time

sys.stdin.read()
print(json.dumps({"type": "turn.started"}), flush=True)
time.sleep(30)
"""
        progress: list[AgentProgress] = []
        agent = ClaudeCliAgent(timeout_seconds=30)

        with self.assertRaises(AgentStopped):
            agent._run_streaming(
                [sys.executable, "-u", "-c", script],
                "test prompt",
                progress.append,
                should_stop=lambda: bool(progress),
            )

        self.assertTrue(progress)


if __name__ == "__main__":
    unittest.main()
