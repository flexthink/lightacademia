from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from lightacademia.git_ops import git_file_at_revision, git_file_history


class GitFileHistoryTest(unittest.TestCase):
    @patch("lightacademia.git_ops.Path.exists", return_value=True)
    @patch("lightacademia.git_ops.run_git")
    def test_reads_file_revisions(self, run_git, exists) -> None:
        run_git.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="abc1234\t1780000000\tLight Academia\tSecond note\nfed5678\t1779990000\tLight Academia\tFirst note\n",
            stderr="",
        )

        history = git_file_history(Path("/project"), Path("/project/Home.md"))

        self.assertEqual([item.subject for item in history], ["Second note", "First note"])
        self.assertEqual(history[0].commit, "abc1234")
        run_git.assert_called_once_with(
            Path("/project"),
            "log",
            "--follow",
            "--max-count=50",
            "--format=%H%x09%ct%x09%an%x09%s",
            "--",
            "Home.md",
            check=False,
        )

    @patch("lightacademia.git_ops.run_git")
    def test_reads_file_content_at_revision(self, run_git) -> None:
        run_git.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="# First\n", stderr="")

        content = git_file_at_revision(Path("/project"), Path("/project/Home.md"), "fed5678")

        self.assertEqual(content, "# First\n")
        run_git.assert_called_once_with(Path("/project"), "show", "fed5678:Home.md")


if __name__ == "__main__":
    unittest.main()
