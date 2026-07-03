from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from lightacademia.git_ops import (
    git_file_at_revision,
    git_file_history,
    git_set_remote_url,
    git_sync,
    git_sync_branch,
)


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

    @patch("lightacademia.git_ops.Path.exists", return_value=True)
    @patch("lightacademia.git_ops.git_remote_url", return_value=None)
    @patch("lightacademia.git_ops.run_git")
    def test_adds_remote_url_when_missing(self, run_git, remote_url, exists) -> None:
        git_set_remote_url(Path("/project"), "git@example.com:repo.git")

        run_git.assert_called_once_with(Path("/project"), "remote", "add", "origin", "git@example.com:repo.git")

    @patch("lightacademia.git_ops.git_has_unmerged_paths", return_value=False)
    @patch("lightacademia.git_ops.git_remote_branch_exists", return_value=True)
    @patch("lightacademia.git_ops.git_sync_branch", return_value="main")
    @patch("lightacademia.git_ops.git_commit_all", return_value=True)
    @patch("lightacademia.git_ops.git_stage_all", return_value=True)
    @patch("lightacademia.git_ops.git_remote_url", return_value="git@example.com:repo.git")
    @patch("lightacademia.git_ops.run_git")
    def test_sync_pulls_and_pushes(
        self,
        run_git,
        remote_url,
        stage_all,
        commit_all,
        sync_branch,
        remote_branch_exists,
        has_unmerged,
    ) -> None:
        run_git.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        result = git_sync(Path("/project"))

        self.assertTrue(result.committed)
        self.assertTrue(result.pushed)
        self.assertFalse(result.conflicts)
        self.assertEqual(
            [call.args for call in run_git.call_args_list],
            [
                (Path("/project"), "pull", "--no-rebase", "origin", "main"),
                (Path("/project"), "push", "-u", "origin", "main"),
            ],
        )

    @patch("lightacademia.git_ops.git_has_unmerged_paths", return_value=True)
    @patch("lightacademia.git_ops.git_remote_branch_exists", return_value=True)
    @patch("lightacademia.git_ops.git_sync_branch", return_value="main")
    @patch("lightacademia.git_ops.git_commit_all", return_value=False)
    @patch("lightacademia.git_ops.git_stage_all", return_value=True)
    @patch("lightacademia.git_ops.git_remote_url", return_value="git@example.com:repo.git")
    @patch("lightacademia.git_ops.run_git")
    def test_sync_treats_pull_conflicts_as_expected(
        self,
        run_git,
        remote_url,
        stage_all,
        commit_all,
        sync_branch,
        remote_branch_exists,
        has_unmerged,
    ) -> None:
        run_git.return_value = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="conflict")

        result = git_sync(Path("/project"))

        self.assertTrue(result.conflicts)
        self.assertFalse(result.pushed)
        run_git.assert_called_once_with(Path("/project"), "pull", "--no-rebase", "origin", "main", check=False)

    @patch("lightacademia.git_ops.git_remote_default_branch", return_value="main")
    @patch("lightacademia.git_ops.git_remote_branch_exists", return_value=False)
    @patch("lightacademia.git_ops.git_current_branch", return_value="master")
    @patch("lightacademia.git_ops.run_git")
    def test_sync_branch_renames_local_master_to_main_when_remote_uses_main(
        self,
        run_git,
        current_branch,
        remote_branch_exists,
        remote_default_branch,
    ) -> None:
        self.assertEqual(git_sync_branch(Path("/project"), "origin"), "main")
        run_git.assert_called_once_with(Path("/project"), "branch", "-M", "main")

    @patch("lightacademia.git_ops.git_remote_branch_exists", return_value=False)
    @patch("lightacademia.git_ops.git_sync_branch", return_value="main")
    @patch("lightacademia.git_ops.git_commit_all", return_value=False)
    @patch("lightacademia.git_ops.git_stage_all", return_value=True)
    @patch("lightacademia.git_ops.git_remote_url", return_value="git@example.com:repo.git")
    @patch("lightacademia.git_ops.run_git")
    def test_sync_pushes_without_pull_when_remote_branch_is_missing(
        self,
        run_git,
        remote_url,
        stage_all,
        commit_all,
        sync_branch,
        remote_branch_exists,
    ) -> None:
        run_git.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        result = git_sync(Path("/project"))

        self.assertFalse(result.pulled)
        self.assertTrue(result.pushed)
        run_git.assert_called_once_with(Path("/project"), "push", "-u", "origin", "main", check=False)


if __name__ == "__main__":
    unittest.main()
