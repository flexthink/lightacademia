from __future__ import annotations

import unittest
from types import SimpleNamespace

from lightacademia.agent_progress import ActivityUpdate, AgentProgress, finish_activities, reduce_progress
from lightacademia.agents import codex_event_from_notification, codex_progress_from_event


def item_event(kind, phase="started", item_id="one", **fields):
    return {"type": f"item.{phase}", "item": {"id": item_id, "type": kind, **fields}}


def apply_events(*events):
    activities = []
    for event in events:
        progress = codex_progress_from_event(event)
        if progress is not None:
            activities = reduce_progress(activities, progress)
    return activities


class AgentProgressTest(unittest.TestCase):
    def test_reasoning_lifecycle_is_one_updating_activity(self):
        entries = apply_events(
            item_event("reasoning"),
            {"type": "item.reasoning.summaryTextDelta", "item_id": "one", "delta": "Inspect "},
            {"type": "item.reasoning.summaryTextDelta", "item_id": "one", "delta": "the files."},
            item_event("reasoning", "completed", summary=["Inspect the files."]),
        )
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].text, "Inspect the files.")
        self.assertEqual(entries[0].state, "complete")

    def test_reasoning_channels_and_summary_parts_do_not_duplicate_each_other(self):
        entries = apply_events(
            item_event("reasoning"),
            {"type": "item.reasoning.textDelta", "item_id": "one", "delta": "Other content"},
            {"type": "item.reasoning.summaryTextDelta", "item_id": "one", "summary_index": 1, "delta": "Second"},
            {"type": "item.reasoning.summaryTextDelta", "item_id": "one", "summary_index": 0, "delta": "First"},
        )
        self.assertEqual(entries[0].text, "First\n\nSecond")

    def test_interleaved_reply_items_keep_separate_deltas(self):
        entries = apply_events(
            {"type": "item.agentMessage.delta", "item_id": "a", "delta": "Hello "},
            {"type": "item.agentMessage.delta", "item_id": "b", "delta": "Separate"},
            {"type": "item.agentMessage.delta", "item_id": "a", "delta": "world"},
            item_event("agent_message", "completed", "a", text="Hello world"),
        )
        self.assertEqual([x.text for x in entries], ["Hello world", "Separate"])
        self.assertEqual(entries[0].state, "complete")

    def test_late_delta_does_not_duplicate_final_reply(self):
        entries = apply_events(
            item_event("agent_message", "completed", text="Final answer"),
            {"type": "item.agentMessage.delta", "item_id": "one", "delta": "answer"},
        )
        self.assertEqual(entries[0].text, "Final answer")
        self.assertEqual(entries[0].state, "complete")

    def test_command_output_survives_completion_without_output_snapshot(self):
        entries = apply_events(
            item_event("command_execution", command="ls data"),
            {"type": "item.commandExecution.outputDelta", "item_id": "one", "delta": "file.csv\n"},
            item_event("command_execution", "completed", command="ls data", exit_code=0),
        )
        self.assertEqual(len(entries), 1)
        self.assertIn("$ ls data", entries[0].text)
        self.assertIn("file.csv", entries[0].text)
        self.assertIn("Exit code: 0", entries[0].text)
        self.assertEqual(entries[0].state, "complete")

    def test_completed_command_snapshot_does_not_repeat_streamed_output(self):
        entries = apply_events(
            item_event("command_execution", command="echo test"),
            {"type": "item.commandExecution.outputDelta", "item_id": "one", "delta": "test\n"},
            item_event("command_execution", "completed", command="echo test", aggregated_output="test\n", exit_code=0),
        )
        self.assertEqual(entries[0].content["output"], "test\n")

    def test_command_failure_is_an_error_activity(self):
        entries = apply_events(item_event("command_execution", "completed", command="false", exit_code=1))
        self.assertEqual(entries[0].state, "error")

    def test_empty_reasoning_snapshot_keeps_streamed_summary(self):
        entries = apply_events(
            {"type": "item.reasoning.summaryTextDelta", "item_id": "one", "delta": "Public summary"},
            item_event("reasoning", "completed", summary=[], content=[]),
        )
        self.assertEqual(entries[0].text, "Public summary")
        self.assertEqual(entries[0].state, "complete")

    def test_turn_lifecycle_is_not_displayed_but_finishes_open_steps(self):
        entries = apply_events(
            {"type": "turn.started"}, item_event("reasoning"),
            {"type": "turn.completed", "turn": {"status": "completed"}},
        )
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].state, "complete")

    def test_failed_turn_closes_open_steps_and_preserves_error(self):
        entries = apply_events(
            item_event("reasoning"),
            {"type": "turn.completed", "turn": {"status": "failed", "error": {"message": "Quota exhausted"}}},
        )
        self.assertTrue(all(x.state == "error" for x in entries))
        self.assertEqual(entries[-1].text, "Quota exhausted")

    def test_finish_does_not_overwrite_existing_tool_error(self):
        entries = apply_events(item_event("reasoning"), item_event("command_execution", "completed", "cmd", exit_code=1))
        done = finish_activities(entries)
        self.assertEqual([x.state for x in done], ["complete", "error"])
        self.assertEqual(entries[0].state, "running")

    def test_mcp_progress_keeps_tool_identity(self):
        entries = apply_events(
            item_event("mcp_tool_call", server="github", tool="search"),
            {"type": "item.mcpToolCall.progress", "item_id": "one", "message": "Checking results"},
            item_event("mcp_tool_call", "completed", server="github", tool="search", result={"count": 2}),
        )
        self.assertEqual(entries[0].label, "github.search")
        self.assertIn("Checking results", entries[0].text)
        self.assertIn("count", entries[0].text)
        self.assertEqual(entries[0].state, "complete")

    def test_file_changes_are_readable_paths_and_diffs(self):
        entries = apply_events(item_event("file_change", "completed", changes=[{"path": "note.md", "diff": "+new text"}]))
        self.assertEqual(entries[0].text, "note.md\n+new text")

    def test_plain_progress_remains_supported(self):
        entries = reduce_progress([], AgentProgress("assistant", "Checking files"))
        self.assertEqual(entries[0].kind, "log")
        self.assertEqual(entries[0].text, "Checking files")

    def test_reducer_does_not_mutate_earlier_snapshots(self):
        progress = codex_progress_from_event({"type": "item.agentMessage.delta", "item_id": "one", "delta": "A"})
        first = reduce_progress([], progress)
        second = reduce_progress(first, progress)
        self.assertEqual(first[0].text, "A")
        self.assertEqual(second[0].text, "AA")

    def test_single_large_output_and_item_count_are_bounded(self):
        entry = AgentProgress("output", "", ActivityUpdate("cmd", "command", "Command", content={"output": "x" * 1000}))
        entries = reduce_progress([], entry, max_chars=100)
        self.assertEqual(len(entries[0].text), 100)
        for _ in range(10):
            entries = reduce_progress(entries, AgentProgress("log", "entry"), max_items=3)
        self.assertEqual(len(entries), 3)

    def test_unknown_notifications_are_not_rendered_as_raw_json(self):
        self.assertIsNone(codex_progress_from_event({"type": "thread.tokenUsage.updated", "unexpected": "payload"}))

    def test_real_sdk_delta_payload_normalizes_snake_case_fields(self):
        try:
            from openai_codex.models import AgentMessageDeltaNotification, Notification
        except ImportError:
            self.skipTest("Optional Codex SDK is not installed")
        notification = Notification(method="item/agentMessage/delta", payload=AgentMessageDeltaNotification(
            delta="Hello", itemId="reply", threadId="thread", turnId="turn",
        ))
        event = codex_event_from_notification(notification)
        progress = codex_progress_from_event(event)
        self.assertEqual(progress.activity.item_id, "reply")
        self.assertTrue(progress.activity.append)
        self.assertEqual(progress.activity.content["text"], "Hello")


if __name__ == "__main__":
    unittest.main()
