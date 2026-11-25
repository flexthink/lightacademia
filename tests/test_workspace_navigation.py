"""Regression coverage for stale project routes causing navigation rerun loops."""
import unittest

from streamlit.testing.v1 import AppTest


class WorkspaceNavigationTest(unittest.TestCase):
    def test_route_event_is_consumed_and_queued_route_wins(self):
        script = '''
from unittest.mock import patch
import streamlit as st
from app import init_state, workspace_navigation_route
init_state()
if "component_results" not in st.session_state:
    st.session_state.component_results = [
        {"route_requested": {"project": "Research", "note": "Home.md"}},
        {"route_requested": None},
    ]
def mount(**kwargs):
    st.session_state.mount_args = kwargs
    return st.session_state.component_results.pop(0) if st.session_state.component_results else {"route_requested": None}
with patch("app.workspace_navigation_component", return_value=mount):
    route = workspace_navigation_route(mode="projects", project_name=st.session_state.project_name or ".daily-notes",
                                      note_name="Home.md", key="navigation")
st.session_state.project_name = route.project
st.text(route.project)
'''
        app = AppTest.from_string(script, default_timeout=20).run()
        self.assertEqual(list(app.exception), [])
        self.assertEqual(app.text[0].value, "Research")
        self.assertNotIn("default", app.session_state.mount_args)
        self.assertIn("on_route_requested_change", app.session_state.mount_args)
        app.run()
        self.assertEqual(app.text[0].value, "Research")
        app.session_state.pending_hash_route = {"project": ".daily-notes", "note": "2026-11-24.md"}
        app.session_state.component_results = [{"route_requested": {"project": "Research", "note": "Home.md"}}]
        app.run()
        self.assertEqual(list(app.exception), [])
        self.assertEqual(app.text[0].value, ".daily-notes")
        app.run()
        self.assertEqual(app.text[0].value, ".daily-notes")
