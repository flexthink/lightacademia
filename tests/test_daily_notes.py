from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from lightacademia.storage import (
    DAILY_NOTES_PROJECT, daily_notes_project, daily_notes_by_date,
    get_or_create_daily_note, list_projects,
)


class DailyNotesTest(unittest.TestCase):
    @patch("lightacademia.storage.git_init")
    def test_private_workspace_and_idempotent_creation(self, git_init):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            project = daily_notes_project(root)
            self.assertEqual(project.name, DAILY_NOTES_PROJECT)
            self.assertEqual(list_projects(root), [])
            self.assertFalse((project.path / "Home.md").exists())
            first = get_or_create_daily_note(project, date(2026, 11, 24))
            self.assertEqual(first.name, "2026-11-24.md")
            self.assertEqual(first.path.read_text(), "# 2026-11-24\n\n")
            first.path.write_text("My existing text")
            self.assertEqual(get_or_create_daily_note(project, date(2026, 11, 24)), first)
            self.assertEqual(first.path.read_text(), "My existing text")
            second = get_or_create_daily_note(project, date(2026, 11, 25))
            (project.path / "invalid.md").write_text("")
            (project.path / "2026-02-30.md").write_text("")
            self.assertEqual(daily_notes_by_date(project), {
                date(2026, 11, 24): first, date(2026, 11, 25): second,
            })

    def test_existing_daily_workspace_does_not_reinitialize_git(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("lightacademia.storage.git_init") as initialize:
                project = daily_notes_project(root)
                initialize.assert_called_once_with(project.path)
                (project.path / ".git").mkdir()
                initialize.reset_mock()
                daily_notes_project(root)
                initialize.assert_not_called()


class DailyNotesUITest(unittest.TestCase):
    def test_add_reopens_today_and_calendar_marks_and_opens_it(self):
        from streamlit.testing.v1 import AppTest
        with TemporaryDirectory() as directory:
            script = '''
from pathlib import Path
from unittest.mock import patch
from datetime import date
import streamlit as st
from app import init_state, render_daily_notes_controls
from lightacademia.storage import daily_notes_project
init_state()
with patch("lightacademia.storage.git_init"):
    project = daily_notes_project(Path(ROOT))
def navigate(*args, **kwargs):
    st.session_state.opened_note = kwargs["next_note"]
with patch("app.commit_before_navigation", side_effect=navigate):
    render_daily_notes_controls(project, project, None)
'''.replace('ROOT', repr(directory))
            app = AppTest.from_string(script, default_timeout=20).run()
            app.button(key="add_daily_note").click().run()
            self.assertEqual(list(app.exception), [])
            self.assertEqual(app.session_state.opened_note, f"{date.today().isoformat()}.md")
            path = Path(directory) / DAILY_NOTES_PROJECT / app.session_state.opened_note
            path.write_text("Keep this text")
            app.button(key="add_daily_note").click().run()
            self.assertEqual(path.read_text(), "Keep this text")
            app.button(key="daily_notes_calendar").click().run()
            self.assertEqual(list(app.exception), [])
            today = app.button(key=f"daily_calendar_{date.today().isoformat()}")
            self.assertIn("●", today.label)
            self.assertFalse(today.disabled)
            today.click().run()
            self.assertEqual(list(app.exception), [])
            calendar_script = script.replace(
                "from app import init_state, render_daily_notes_controls",
                "from app import init_state, daily_notes_calendar_dialog",
            ).replace(
                "render_daily_notes_controls(project, project, None)",
                "daily_notes_calendar_dialog(project, project, None)",
            )
            calendar_app = AppTest.from_string(calendar_script, default_timeout=20).run()
            calendar_app.button(key="daily_previous_month").click().run()
            self.assertEqual(list(calendar_app.exception), [])
            self.assertNotEqual(calendar_app.session_state.daily_calendar_month.month, date.today().month)
            calendar_app.button(key="daily_next_month").click().run()
            self.assertEqual(list(calendar_app.exception), [])
            self.assertEqual(calendar_app.session_state.daily_calendar_month.month, date.today().month)
