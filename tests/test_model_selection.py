from __future__ import annotations

import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


MODEL_PICKER_SCRIPT = """
from unittest.mock import patch
import streamlit as st
from app import preferred_agent_model, render_agent_model_selector
from lightacademia.agents import AgentError

st.checkbox("Show Robot form", value=True, key="show_form")
models = st.session_state.get("models", ["powerful-model", "other-model", "gpt-reserve"])
lookup = {"side_effect": AgentError("Lookup failed")} if st.session_state.get("lookup_failed") else {"return_value": models}
with patch("app.selectable_agent_models", **lookup):
    if st.session_state.show_form:
        st.session_state.selected_model = render_agent_model_selector(
            "codex", st.session_state.get("configured_model")
        )
st.session_state.request_model = preferred_agent_model(
    "codex", st.session_state.get("configured_model")
)
"""


class ModelSelectionTest(unittest.TestCase):
    def picker(self) -> AppTest:
        return AppTest.from_string(MODEL_PICKER_SCRIPT, default_timeout=10)

    def assert_model(self, app: AppTest, expected: str) -> None:
        self.assertEqual(list(app.exception), [])
        self.assertEqual(app.selectbox(key="agent_model_codex").value, expected)
        self.assertEqual(app.session_state.selected_model, expected)

    def test_reserve_selected_even_when_powerful_model_is_first(self) -> None:
        app = self.picker().run()
        self.assert_model(app, "gpt-reserve")
        self.assertEqual(app.session_state.request_model, "gpt-reserve")
        self.assertEqual(app.selectbox[0].options[0], "powerful-model")

    def test_choice_sticks_without_submitting_a_robot_request(self) -> None:
        app = self.picker().run()
        app.selectbox[0].select("other-model").run()
        app.run()
        self.assert_model(app, "other-model")
        self.assertEqual(app.session_state.request_model, "other-model")

    def test_choice_survives_hidden_form_and_recreated_widget(self) -> None:
        app = self.picker().run()
        app.selectbox[0].select("other-model").run()
        app.checkbox[0].uncheck().run()
        self.assertEqual(list(app.exception), [])
        self.assertEqual(len(app.selectbox), 0)
        # Also exercise reconstruction from the durable session preference.
        del app.session_state["agent_model_codex"]
        app.checkbox[0].check().run()
        self.assert_model(app, "other-model")

    def test_new_session_starts_with_reserve(self) -> None:
        first = self.picker().run()
        first.selectbox[0].select("other-model").run()
        second = self.picker().run()
        self.assert_model(second, "gpt-reserve")
        self.assert_model(first.run(), "other-model")

    def test_choice_survives_reordered_and_incomplete_model_lists(self) -> None:
        app = self.picker().run()
        app.selectbox[0].select("other-model").run()
        app.session_state.models = ["new-powerful-model", "gpt-reserve", "other-model"]
        self.assert_model(app.run(), "other-model")
        app.session_state.models = ["new-powerful-model", "gpt-reserve"]
        self.assert_model(app.run(), "other-model")

    def test_reserve_is_available_when_discovery_omits_it(self) -> None:
        app = self.picker()
        app.session_state.models = ["powerful-model"]
        self.assert_model(app.run(), "gpt-reserve")

    def test_lookup_failure_keeps_default_or_session_choice(self) -> None:
        app = self.picker()
        app.session_state.lookup_failed = True
        app.run()
        self.assertEqual(list(app.exception), [])
        self.assertEqual(app.session_state.selected_model, "gpt-reserve")
        app.session_state.lookup_failed = False
        app.run().selectbox[0].select("other-model").run()
        app.session_state.lookup_failed = True
        app.run()
        self.assertEqual(list(app.exception), [])
        self.assertEqual(app.session_state.selected_model, "other-model")
        self.assertEqual(app.session_state.request_model, "other-model")

    def test_explicit_startup_model_is_overridden_by_session_choice(self) -> None:
        app = self.picker()
        app.session_state.configured_model = "startup-model"
        self.assert_model(app.run(), "startup-model")
        app.selectbox[0].select("other-model").run()
        self.assert_model(app.run(), "other-model")
        self.assertEqual(app.session_state.request_model, "other-model")

    def test_command_line_default_is_reserve(self) -> None:
        from app import get_config

        with patch("sys.argv", ["app.py"]):
            self.assertEqual(get_config.__wrapped__().codex_model, "gpt-reserve")
        with patch("sys.argv", ["app.py", "--codex-model", "startup-model"]):
            self.assertEqual(get_config.__wrapped__().codex_model, "startup-model")


if __name__ == "__main__":
    unittest.main()
