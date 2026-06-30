from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import mock_open, patch

from tools.launch_experiment import (
    append_manifest,
    build_launch_command,
    build_mkdir_command,
    build_resume_command,
    build_upload_command,
    default_experiment_name,
    main,
    rsync_remote_path,
)


class LaunchExperimentTest(unittest.TestCase):
    def test_default_experiment_name_uses_script_stem(self) -> None:
        self.assertEqual(default_experiment_name(Path("scripts/train.sh")), "train-1")

    def test_builds_remote_commands(self) -> None:
        self.assertEqual(
            build_mkdir_command("fir", "~/lightacademia/experiments"),
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "fir",
                'bash -l -c \'mkdir -p "$HOME"/lightacademia/experiments\'',
            ],
        )
        self.assertEqual(
            build_upload_command("fir", Path("train.sh"), "~/lightacademia/experiments"),
            [
                "rsync",
                "-av",
                "-e",
                "ssh -o BatchMode=yes",
                "train.sh",
                "fir:$HOME/lightacademia/experiments/",
            ],
        )

        launch = build_launch_command("fir", "train.sh", "train-1", "~/lightacademia/experiments")

        self.assertEqual(launch[:4], ["ssh", "-o", "BatchMode=yes", "fir"])
        self.assertEqual(len(launch), 5)
        self.assertIn("bash -l -c", launch[4])
        self.assertIn('cd "$HOME"/lightacademia/experiments', launch[4])
        self.assertIn('python "$HOME"/scripts/experiment.py', launch[4])
        self.assertIn('--scripts "$HOME"/lightacademia/experiments', launch[4])
        self.assertIn("--script train.sh", launch[4])
        self.assertIn("--name train-1", launch[4])

    def test_rewrites_only_lightacademia_tilde_for_rsync_paths(self) -> None:
        self.assertEqual(
            rsync_remote_path("~/lightacademia/experiments"),
            "$HOME/lightacademia/experiments",
        )
        self.assertEqual(rsync_remote_path("~/experiments"), "~/experiments")

    def test_builds_resume_command_with_name_only(self) -> None:
        resume = build_resume_command("fir", "train-1")

        self.assertEqual(resume[:4], ["ssh", "-o", "BatchMode=yes", "fir"])
        self.assertEqual(len(resume), 5)
        self.assertIn("bash -l -c", resume[4])
        self.assertIn('python "$HOME"/scripts/experiment.py', resume[4])
        self.assertIn("--name train-1", resume[4])
        self.assertNotIn("cd ", resume[4])
        self.assertNotIn("--script", resume[4])
        self.assertNotIn("--scripts", resume[4])

    def test_append_manifest_writes_launch_log_entry(self) -> None:
        opened = mock_open()
        with (
            patch("tools.launch_experiment.Path.mkdir") as mkdir,
            patch("tools.launch_experiment.Path.open", opened),
        ):
            append_manifest(
                Path("data/experiments/launch-log.jsonl"),
                cluster="fir",
                local_script=Path("train.sh"),
                name="train-1",
                commands={"launch": ["ssh", "fir", "python"]},
                stage="complete",
                returncode=0,
                scripts_path="~/lightacademia/experiments",
            )
        mkdir.assert_called_once_with(parents=True, exist_ok=True)
        written = opened().write.call_args.args[0]
        entry = json.loads(written)

        self.assertEqual(entry["cluster"], "fir")
        self.assertEqual(entry["experiment"], "train-1")
        self.assertEqual(entry["operation"], "launch")
        self.assertEqual(entry["script"], "train.sh")
        self.assertEqual(entry["destination"], "fir:$HOME/lightacademia/experiments/train.sh")
        self.assertEqual(entry["scripts_path"], "~/lightacademia/experiments")
        self.assertEqual(entry["stage"], "complete")
        self.assertEqual(entry["returncode"], 0)
        self.assertIn("launched_at", entry)

    @patch("tools.launch_experiment.append_manifest")
    @patch("tools.launch_experiment.Path.is_file", return_value=True)
    @patch("tools.launch_experiment.subprocess.run")
    @patch.dict(
        "tools.launch_experiment.os.environ",
        {
            "LIGHTACADEMIA_CLUSTERS": "fir rorqual",
            "LIGHTACADEMIA_LAUNCH_SCRIPTS_PATH": "~/custom/experiments",
        },
    )
    def test_main_logs_successful_launch(self, run, is_file, append_manifest_mock) -> None:
        run.return_value = subprocess.CompletedProcess(args=[], returncode=0)

        with patch("tools.launch_experiment.sys.argv", ["launch-experiment.sh", "fir", "train.sh"]):
            self.assertEqual(main(), 0)

        self.assertEqual(run.call_count, 3)
        launch_command = run.call_args_list[2].args[0]
        self.assertEqual(len(launch_command), 5)
        self.assertIn('--scripts "$HOME"/custom/experiments', launch_command[4])
        is_file.assert_called_once()
        append_manifest_mock.assert_called_once()
        self.assertEqual(append_manifest_mock.call_args.kwargs["stage"], "complete")
        self.assertEqual(append_manifest_mock.call_args.kwargs["returncode"], 0)
        self.assertEqual(append_manifest_mock.call_args.kwargs["name"], "train-1")
        self.assertEqual(append_manifest_mock.call_args.kwargs["scripts_path"], "~/custom/experiments")

    @patch("tools.launch_experiment.append_manifest")
    @patch("tools.launch_experiment.subprocess.run")
    @patch.dict("tools.launch_experiment.os.environ", {"LIGHTACADEMIA_CLUSTERS": "fir rorqual"})
    def test_main_resumes_experiment_by_name_only(self, run, append_manifest_mock) -> None:
        run.return_value = subprocess.CompletedProcess(args=[], returncode=0)

        with patch("tools.launch_experiment.sys.argv", ["launch-experiment.sh", "fir", "--resume", "train-1"]):
            self.assertEqual(main(), 0)

        run.assert_called_once()
        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["ssh", "-o", "BatchMode=yes", "fir"])
        self.assertEqual(len(command), 5)
        self.assertIn("bash -l -c", command[4])
        self.assertIn("--name train-1", command[4])
        self.assertNotIn("--script", command[4])
        append_manifest_mock.assert_called_once()
        self.assertIsNone(append_manifest_mock.call_args.kwargs["local_script"])
        self.assertEqual(append_manifest_mock.call_args.kwargs["operation"], "resume")
        self.assertEqual(append_manifest_mock.call_args.kwargs["stage"], "complete")


if __name__ == "__main__":
    unittest.main()
