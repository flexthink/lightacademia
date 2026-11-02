from __future__ import annotations

import unittest

from streamlit.testing.v1 import AppTest


CHAT_PANEL_SCRIPT = """
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import streamlit as st
from app import apply_requested_action, init_state, render_agent_panel
from lightacademia.actions import NoteAction
from lightacademia.storage import Project

init_state()
project = Project("Research", Path("/test-project"))
note = SimpleNamespace(name="Note.md")
config = SimpleNamespace(agent=st.session_state.get("agent", "codex"), codex_model="gpt-reserve", agent_timeout_seconds=60)
actions = (NoteAction("Summarize", "Summarize this note.", 1, 5),) if st.session_state.get("has_actions") else ()
if st.session_state.pop("request_action", False):
    st.session_state.requested_action = actions[0]
apply_requested_action(note, actions)

def start_robot(*args, **kwargs):
    st.session_state.request = {
        "prompt": args[2], "model": args[5], "mode": kwargs.get("mode", "action"),
        "chat_key": kwargs.get("chat_key"), "chat_details": kwargs.get("chat_details")
    }
    st.session_state.working = True

run = object() if st.session_state.get("working") else None
with (
    patch("app.get_config", return_value=config),
    patch("app.selectable_agent_models", return_value=["gpt-reserve", "other-model"]),
    patch("app.start_agent_command", side_effect=start_robot),
    patch("app.active_agent_run", return_value=run),
    patch("app.active_gardener_script_run", return_value=None),
    patch("app.render_agent_progress_panel", side_effect=lambda: st.write("Robot is working")),
):
    render_agent_panel(project, note, actions, Path("/test-tools"))
"""


class ChatModeTest(unittest.TestCase):
    def panel(self, *, actions: bool = False) -> AppTest:
        app = AppTest.from_string(CHAT_PANEL_SCRIPT, default_timeout=10)
        app.session_state.has_actions = actions
        return app.run()

    def assert_chat(self, app: AppTest) -> None:
        self.assertEqual(list(app.exception), [])
        self.assertEqual(len(app.radio), 0)
        self.assertEqual(len(app.chat_input), 1)
        self.assertEqual(len(app.text_area), 0)

    def test_chat_returns_after_robot_run_hides_the_form(self) -> None:
        app = self.panel()
        self.assert_chat(app)
        for _ in range(2):
            app.session_state.working = True
            app.run()
            self.assertEqual(list(app.exception), [])
            self.assertEqual(len(app.chat_input), 0)
            app.session_state.working = False
            self.assert_chat(app.run())

    def test_legacy_action_mode_does_not_replace_chat(self) -> None:
        app = self.panel()
        app.session_state.robot_mode = "Action"
        self.assert_chat(app.run())

    def test_settings_contains_model_without_mode_toggle(self) -> None:
        app = self.panel()
        settings = app.get_by_key("robot_settings")
        self.assertEqual(len(settings.radio), 0)
        self.assertEqual(settings.selectbox[0].value, "gpt-reserve")
        self.assertEqual(len(app.button), 0)

    def test_lightning_selector_contains_note_actions(self) -> None:
        app = self.panel(actions=True)
        self.assert_chat(app)
        actions = app.get_by_key("robot_actions_Note.md_0")
        self.assertEqual(actions.selectbox[0].options, ["Choose an action...", "Summarize"])
        self.assertTrue(actions.button[0].disabled)
        actions.selectbox[0].select_index(1).run()
        self.assert_chat(app)
        self.assertFalse(app.button[0].disabled)
        self.assertIn("Summarize", app.chat_input[0].proto.placeholder)

    def test_chat_submission_uses_settings_and_preserves_them_after_run(self) -> None:
        app = self.panel()
        app.selectbox(key="agent_model_codex").select("other-model").run()
        app.chat_input[0].set_value("Hello Robot").run()
        self.assertEqual(list(app.exception), [])
        self.assertEqual(app.session_state.request["prompt"], "Hello Robot")
        self.assertEqual(app.session_state.request["model"], "other-model")
        self.assertEqual(app.session_state.request["mode"], "chat")
        app.session_state.working = False
        self.assert_chat(app.run())
        self.assertEqual(app.selectbox(key="agent_model_codex").value, "other-model")
        self.assertEqual(app.chat_message[0].markdown[0].value, "Hello Robot")

    def test_main_view_action_opens_lightning_selector_and_runs_in_chat(self) -> None:
        app = self.panel(actions=True)
        app.session_state.request_action = True
        self.assert_chat(app.run())
        self.assertTrue(app.session_state["robot_actions_Note.md_0"])
        self.assertEqual(app.selectbox(key="selected_action_Note.md_0").value.name, "Summarize")
        app.button[0].click().run()
        self.assertEqual(list(app.exception), [])
        self.assertEqual(app.session_state.request["mode"], "chat")
        self.assertIn("Summarize this note.", app.session_state.request["prompt"])
        app.session_state.working = False
        self.assert_chat(app.run())
        self.assertIsNone(app.selectbox(key="selected_action_Note.md_0").value)
        self.assertEqual(app.chat_message[0].markdown[0].value, "⚡ Summarize")
        # A follow-up is ordinary chat, without rerunning the previous action.
        app.chat_input[0].set_value("Explain the result").run()
        self.assertEqual(app.session_state.request["prompt"], "Explain the result")

    def test_action_and_extra_instructions_use_existing_chat_thread(self) -> None:
        app = self.panel(actions=True)
        chat_key = "/test-project::Note.md"
        app.session_state.agent_chats[chat_key]["details"] = {"thread_id": "existing-thread"}
        app.selectbox(key="selected_action_Note.md_0").select_index(1).run()
        app.chat_input[0].set_value("Use three bullet points").run()
        self.assertEqual(list(app.exception), [])
        request = app.session_state.request
        self.assertEqual(request["mode"], "chat")
        self.assertEqual(request["chat_key"], chat_key)
        self.assertEqual(request["chat_details"], {"thread_id": "existing-thread"})
        self.assertIn("Summarize this note.", request["prompt"])
        self.assertIn("Use three bullet points", request["prompt"])

    def test_clearing_selected_action_sends_plain_message(self) -> None:
        app = self.panel(actions=True)
        app.selectbox(key="selected_action_Note.md_0").select_index(1).run()
        app.selectbox(key="selected_action_Note.md_0").select_index(0).run()
        app.chat_input[0].set_value("Just a question").run()
        self.assertEqual(list(app.exception), [])
        self.assertEqual(app.session_state.request["prompt"], "Just a question")

    def test_agent_without_chat_support_keeps_compact_action_form(self) -> None:
        app = AppTest.from_string(CHAT_PANEL_SCRIPT, default_timeout=10)
        app.session_state.agent = "claude"
        app.run()
        self.assertEqual(list(app.exception), [])
        self.assertEqual(len(app.radio), 0)
        self.assertEqual(len(app.chat_input), 0)
        self.assertEqual(len(app.text_area), 1)
        self.assertEqual(app.button[0].label, "Run robot")
        self.assertEqual(len(app.selectbox), 1)
        app.text_area[0].set_value("Do this task").run()
        app.button[0].click().run()
        self.assertEqual(list(app.exception), [])
        self.assertEqual(app.session_state.request["mode"], "action")


if __name__ == "__main__":
    unittest.main()
