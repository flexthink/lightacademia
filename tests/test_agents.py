from __future__ import annotations

import sys
import unittest
from pathlib import Path

from lightacademia.agents import (
    AgentContext,
    AgentProgress,
    CodexCliAgent,
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


if __name__ == "__main__":
    unittest.main()
