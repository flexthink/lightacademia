from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.fetch_experiment_data import (
    build_list_command,
    build_rsync_command,
    list_experiments,
    validate_experiment_pattern,
)


class FetchExperimentDataTest(unittest.TestCase):
    def test_builds_remote_experiment_listing(self) -> None:
        command = build_list_command("fir", "~/experiments", "paintbrush-*")

        self.assertEqual(command[:4], ["ssh", "-o", "BatchMode=yes", "fir"])
        self.assertIn('cd "$HOME"/experiments', command[4])
        self.assertIn("for experiment in paintbrush-*/", command[4])
        self.assertIn("done; true; }", command[4])

    @patch("tools.fetch_experiment_data.subprocess.run")
    def test_lists_and_deduplicates_matching_experiments(self, run) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="paintbrush-b/\npaintbrush-a/\npaintbrush-a/\n",
            stderr="",
        )

        experiments, _ = list_experiments("fir", "~/experiments", "paintbrush-*")

        self.assertEqual(experiments, ["paintbrush-a", "paintbrush-b"])
        run.assert_called_once()

    @patch("tools.fetch_experiment_data.subprocess.run")
    def test_no_matches_is_an_empty_list(self, run) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        experiments, _ = list_experiments("fir", "~/experiments", "missing-*")

        self.assertEqual(experiments, [])

    def test_roots_each_rsync_at_one_experiment(self) -> None:
        command = build_rsync_command(
            "fir",
            "~/experiments",
            "paintbrush-a",
            ["**/metrics.csv", "plots/*.png"],
            Path("data/experiments"),
            dry_run=False,
        )

        self.assertNotIn("--relative", command)
        self.assertIn("--include=**/metrics.csv", command)
        self.assertEqual(command[-2], "fir:~/experiments/paintbrush-a/")
        self.assertEqual(command[-1], "data/experiments/fir/paintbrush-a")

    def test_rejects_shell_syntax_and_parent_traversal(self) -> None:
        for pattern in ("run-*; rm -rf /", "../run-*", "$(hostname)"):
            with self.subTest(pattern=pattern):
                with self.assertRaises(ValueError):
                    validate_experiment_pattern(pattern)


if __name__ == "__main__":
    unittest.main()
