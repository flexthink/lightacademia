from __future__ import annotations

import sys
import unittest
from pathlib import Path

from lightacademia.agents import (
    AgentContext,
    AgentProgress,
    AgentStopped,
    ClaudeCliAgent,
    CodexCliAgent,
    create_agent,
    claude_progress_from_event,
    claude_tool_action_from_event,
    codex_progress_from_event,
    codex_tool_action_from_event,
)


class CodexProgressTest(unittest.TestCase):
    def test_builds_prompt_from_external_template(self) -> None:
        agent = CodexCliAgent()
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

    def test_codex_command_enables_workspace_network_access(self) -> None:
        agent = CodexCliAgent(network_access=True)
        context = AgentContext(
            project_dir=Path("/project"),
            project_name="project",
            tools_dir=Path("/tools"),
            current_note="Home.md",
        )

        command = agent._build_command(
            "codex",
            context,
            Path("/temporary/tools"),
            Path("/temporary/last-message.md"),
        )

        self.assertIn("sandbox_workspace_write.network_access=true", command)

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

    def test_create_agent_selects_cli_implementation(self) -> None:
        self.assertIsInstance(create_agent("codex"), CodexCliAgent)
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

    def test_streams_jsonl_subprocess_without_model_call(self) -> None:
        script = """
import json
import sys

sys.stdin.read()
events = [
    {"type": "turn.started"},
    {"type": "item.completed", "item": {"type": "reasoning", "text": "Checking files."}},
    {"type": "item.started", "item": {"type": "command_execution", "command": "ls"}},
    {"type": "item.completed", "item": {"type": "agent_message", "text": "Done."}},
]
for event in events:
    print(json.dumps(event), flush=True)
print("diagnostic", file=sys.stderr, flush=True)
"""
        progress: list[AgentProgress] = []
        agent = CodexCliAgent(timeout_seconds=5)

        result = agent._run_streaming(
            [sys.executable, "-u", "-c", script],
            "test prompt",
            progress.append,
        )

        self.assertEqual(result["returncode"], 0)
        self.assertEqual(result["last_agent_message"], "Done.")
        self.assertEqual(result["tool_actions"], ["ls"])
        self.assertIn("diagnostic", result["stderr"])
        self.assertTrue(any("Checking files" in item.text for item in progress))

    def test_streams_claude_jsonl_subprocess_without_model_call(self) -> None:
        script = """
import json
import sys

sys.stdin.read()
events = [
    {"type": "system", "subtype": "init"},
    {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Checking files."},
        {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
    ]}},
    {"type": "result", "result": "Done."},
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
        )

        self.assertEqual(result["returncode"], 0)
        self.assertEqual(result["last_agent_message"], "Done.")
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
        agent = CodexCliAgent(timeout_seconds=30)

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
