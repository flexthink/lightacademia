from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lightacademia.storage import (
    NOTEBOOK_READY_FILE,
    Note,
    Project,
    create_note,
    create_project,
    initialize_notebook,
    rename_note,
    safe_name,
)


class StorageNamesTest(unittest.TestCase):
    def test_safe_name_preserves_spaces(self) -> None:
        self.assertEqual(safe_name("My Research Notebook"), "My Research Notebook")
        self.assertEqual(safe_name("  Ablation   Results  "), "Ablation Results")

    @patch("lightacademia.storage.git_commit_all")
    @patch("lightacademia.storage.git_init")
    def test_project_and_note_names_preserve_spaces(self, _git_init, _git_commit_all) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            notebook_dir = Path(temporary_directory)
            project = create_project(notebook_dir, "Speech Research")
            note = create_note(project, "Validation Results")

            self.assertEqual(project.name, "Speech Research")
            self.assertEqual(note.name, "Validation Results.md")
            self.assertTrue(note.path.is_file())

    @patch("lightacademia.storage.git_mv")
    def test_note_rename_preserves_spaces(self, git_mv) -> None:
        project = Project("Research", Path("/tmp/Research"))
        note_path = project.path / "Old.md"
        note = Note("Old.md", note_path)
        git_mv.side_effect = lambda _project, source, target: None

        renamed = rename_note(project, note, "New Results")

        self.assertEqual(renamed.name, "New Results.md")
        git_mv.assert_called_once_with(project.path, note_path, project.path / "New Results.md")


class NotebookInitializationTest(unittest.TestCase):
    @patch("lightacademia.storage.git_commit_all")
    @patch("lightacademia.storage.git_init")
    def test_copies_stock_projects_and_marks_notebook_ready(self, git_init, git_commit_all) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            stock = root / "stock"
            notebook = root / "notebook"
            stock_project = stock / "Getting Started"
            stock_project.mkdir(parents=True)
            (stock_project / "Home.md").write_text("# Guide\n", encoding="utf-8")

            initialized = initialize_notebook(notebook, stock)

            self.assertTrue(initialized)
            self.assertEqual((notebook / "Getting Started" / "Home.md").read_text(), "# Guide\n")
            self.assertTrue((notebook / NOTEBOOK_READY_FILE).is_file())
            copied_project = notebook.resolve() / "Getting Started"
            git_init.assert_called_once_with(copied_project)
            git_commit_all.assert_called_once_with(copied_project, "Create stock notebook")

    @patch("lightacademia.storage.git_commit_all")
    @patch("lightacademia.storage.git_init")
    def test_ready_file_skips_stock_copy(self, git_init, git_commit_all) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            stock_project = root / "stock" / "Getting Started"
            stock_project.mkdir(parents=True)
            (stock_project / "Home.md").write_text("# Guide\n", encoding="utf-8")
            notebook = root / "notebook"
            notebook.mkdir()
            (notebook / NOTEBOOK_READY_FILE).touch()

            initialized = initialize_notebook(notebook, root / "stock")

            self.assertFalse(initialized)
            self.assertFalse((notebook / "Getting Started").exists())
            git_init.assert_not_called()
            git_commit_all.assert_not_called()

    @patch("lightacademia.storage.git_commit_all")
    @patch("lightacademia.storage.git_init")
    def test_does_not_overwrite_existing_project(self, git_init, git_commit_all) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            stock_project = root / "stock" / "Existing"
            stock_project.mkdir(parents=True)
            (stock_project / "Home.md").write_text("stock\n", encoding="utf-8")
            existing_project = root / "notebook" / "Existing"
            existing_project.mkdir(parents=True)
            (existing_project / "Home.md").write_text("user\n", encoding="utf-8")

            initialize_notebook(root / "notebook", root / "stock")

            self.assertEqual((existing_project / "Home.md").read_text(), "user\n")
            git_init.assert_not_called()
            git_commit_all.assert_not_called()


if __name__ == "__main__":
    unittest.main()
