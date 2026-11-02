from __future__ import annotations

import unittest

from streamlit.testing.v1 import AppTest

from lightacademia.agent_progress import Activity, AgentProgress, ActivityUpdate, reduce_progress


VIEW_SCRIPT = """
import streamlit as st
from lightacademia.agent_ui import render_agent_activities, render_agent_transcript
if "messages" in st.session_state:
    render_agent_transcript(st.session_state.messages)
else:
    render_agent_activities(st.session_state.get("activities", []))
"""

LIVE_SCRIPT = """
import queue
import threading
from types import SimpleNamespace
from unittest.mock import patch
import streamlit as st
from app import init_state, render_agent_progress_panel
from lightacademia.agents import codex_progress_from_event

init_state()
if "test_run" not in st.session_state:
    run = SimpleNamespace(run_id="live", chat_key="chat", progress=queue.Queue(), stop_requested=threading.Event(), done=False)
    for event in [
        {"type": "item.started", "item": {"id": "r", "type": "reasoning"}},
        {"type": "item.reasoning.summaryTextDelta", "item_id": "r", "delta": "Checking the data"},
        {"type": "item.agentMessage.delta", "item_id": "m", "delta": "Here is the answer"},
    ]:
        run.progress.put(codex_progress_from_event(event))
    st.session_state.test_run = run
    st.session_state.agent_chats = {"chat": {"messages": [{"role": "user", "content": "My question"}], "details": {}}}
with patch("app.active_agent_run", return_value=st.session_state.test_run):
    render_agent_progress_panel()
"""

FINISH_SCRIPT = """
import queue
import threading
from types import SimpleNamespace
from unittest.mock import patch
import streamlit as st
from app import init_state, finish_agent_run
from lightacademia.agents import AgentError, AgentStopped, codex_progress_from_event
from lightacademia.agent_ui import render_agent_transcript

init_state()
error_kind = st.session_state.get("error_kind")
error = AgentStopped("Cancelled") if error_kind == "stop" else AgentError("Failure") if error_kind else None
run = SimpleNamespace(
    run_id="finish", mode="chat", chat_key="chat", chat_details={"thread_id": "saved-thread"},
    progress=queue.Queue(), stop_requested=threading.Event(), error=error, response_message="Done.",
)
for event in [
    {"type": "item.started", "item": {"id": "r", "type": "reasoning"}},
    {"type": "item.completed", "item": {"id": "m", "type": "agent_message", "text": "Done."}},
]:
    run.progress.put(codex_progress_from_event(event))
st.session_state.agent_chats = {"chat": {"messages": [{"role": "user", "content": "My question"}], "details": {}}}
with patch("app.agent_runs", return_value={"finish": run}), patch("app.reload_note_from_disk"):
    finish_agent_run(run)
render_agent_transcript(st.session_state.agent_chats["chat"]["messages"])
"""


class AgentUiTest(unittest.TestCase):
    def test_reasoning_uses_gear_avatar_and_running_status(self):
        app = AppTest.from_string(VIEW_SCRIPT)
        app.session_state.activities = [Activity("r", "reasoning", "Thinking", "running", {"summary:0000": "Inspect the data"})]
        app.run()
        self.assertEqual(list(app.exception), [])
        self.assertEqual(app.chat_message[0].avatar, ":material/settings:")
        self.assertEqual(app.status[0].state, "running")
        self.assertEqual(app.status[0].label, "Thinking")
        self.assertIn("Inspect the data", [x.value for x in app.markdown])
        self.assertEqual(len(app.code), 0)

    def test_progress_updates_one_view_instead_of_appending_start_end_labels(self):
        app = AppTest.from_string(VIEW_SCRIPT)
        activities = reduce_progress([], AgentProgress("start", "", ActivityUpdate("r", "reasoning", "Thinking")))
        app.session_state.activities = activities
        app.run()
        activities = reduce_progress(activities, AgentProgress("delta", "", ActivityUpdate("r", "reasoning", "Thinking", content={"summary:0000": "Checking"}, append=True)))
        activities = reduce_progress(activities, AgentProgress("end", "", ActivityUpdate("r", "reasoning", "Thinking", "complete")))
        app.session_state.activities = activities
        app.run()
        self.assertEqual(list(app.exception), [])
        self.assertEqual(len(app.status), 1)
        self.assertEqual(app.status[0].state, "complete")
        self.assertEqual(app.status[0].label, "Thought")
        self.assertEqual([x.value for x in app.markdown], ["Checking"])

    def test_failed_command_has_expandable_output_and_error_state(self):
        app = AppTest.from_string(VIEW_SCRIPT)
        app.session_state.activities = [Activity("c", "command", "Command", "error", {"command": "$ false", "output": "Failed", "exit_code": "Exit code: 1"})]
        app.run()
        self.assertEqual(list(app.exception), [])
        self.assertEqual(app.status[0].state, "error")
        self.assertTrue(app.status[0].proto.expanded)
        self.assertIn("$ false", app.code[0].value)
        self.assertIn("Exit code: 1", app.code[0].value)

    def test_history_keeps_activity_and_renders_final_reply_once(self):
        app = AppTest.from_string(VIEW_SCRIPT)
        app.session_state.messages = [{"role": "assistant", "content": "Done.", "activities": [
            Activity("r", "reasoning", "Thinking", "complete", {"summary:0000": "Checked data"}),
            Activity("m", "message", "Reply", "complete", {"text": "Done."}),
        ]}]
        app.run()
        self.assertEqual(list(app.exception), [])
        self.assertEqual([x.value for x in app.markdown].count("Done."), 1)
        self.assertEqual(len(app.expander), 1)
        self.assertFalse(app.expander[0].proto.expanded)
        self.assertEqual(len(app.chat_message), 2)

    def test_truncated_activity_does_not_duplicate_a_long_final_reply(self):
        app = AppTest.from_string(VIEW_SCRIPT)
        app.session_state.messages = [{"role": "assistant", "content": "A very long final answer", "activities": [
            Activity("m", "message", "Reply", "complete", {"text": "final answer"}),
        ]}]
        app.run()
        self.assertEqual(list(app.exception), [])
        self.assertEqual([x.value for x in app.markdown], ["A very long final answer"])

    def test_live_panel_shows_question_reasoning_and_reply_with_working_stop_button(self):
        app = AppTest.from_string(LIVE_SCRIPT, default_timeout=10).run()
        self.assertEqual(list(app.exception), [])
        self.assertEqual([x.value for x in app.markdown], ["My question", "Checking the data", "Here is the answer"])
        self.assertEqual(len(app.code), 0)
        self.assertEqual(len(app.session_state.agent_progress_entries), 2)
        app.button[0].click().run()
        self.assertEqual(list(app.exception), [])
        self.assertTrue(app.session_state.test_run.stop_requested.is_set())
        self.assertTrue(app.button[0].disabled)

    def test_finish_persists_complete_activity_without_duplicating_answer(self):
        app = AppTest.from_string(FINISH_SCRIPT, default_timeout=10).run()
        self.assertEqual(list(app.exception), [])
        messages = app.session_state.agent_chats["chat"]["messages"]
        self.assertEqual(len(messages), 2)
        self.assertTrue(all(item.state == "complete" for item in messages[-1]["activities"]))
        self.assertEqual(app.session_state.agent_chats["chat"]["details"]["thread_id"], "saved-thread")
        self.assertEqual([x.value for x in app.markdown].count("Done."), 1)

    def test_failure_and_stop_retain_user_question_and_activity(self):
        for error_kind in ("error", "stop"):
            with self.subTest(error_kind=error_kind):
                app = AppTest.from_string(FINISH_SCRIPT, default_timeout=10)
                app.session_state.error_kind = error_kind
                app.run()
                self.assertEqual(list(app.exception), [])
                messages = app.session_state.agent_chats["chat"]["messages"]
                self.assertEqual(messages[0]["content"], "My question")
                self.assertEqual(messages[-1]["activities"][0].state, "error")
                self.assertTrue(messages[-1]["content"])
                self.assertFalse(app.session_state.agent_running)


if __name__ == "__main__":
    unittest.main()
