from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from lightacademia.gardener import (
    GardenerResult,
    GardenerTask,
    add_gardener_task,
    build_gardener_followup_prompt,
    build_gardener_script_prompt,
    current_gardener_script,
    due_gardener_tasks,
    gardener_hash_comment,
    gardener_script_path,
    load_gardener_tasks,
    mark_gardener_finished,
    mark_gardener_process_started,
    parse_gardener_result,
    request_gardener_run,
    reset_interrupted_gardener_tasks,
    save_gardener_tasks,
    update_gardener_frequency,
)


class GardenerTest(unittest.TestCase):
    def test_adds_task_and_waits_for_its_first_interval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            notebook_dir = Path(temporary_directory)
            task = add_gardener_task(
                notebook_dir,
                project_name="Research",
                note_name="Experiments.md",
                action_name="Refresh experiments",
                instructions="Fetch the latest experiments.",
                frequency_minutes=30,
            )

            loaded = load_gardener_tasks(notebook_dir)

            self.assertEqual(loaded, [task])
            self.assertEqual(due_gardener_tasks(notebook_dir, datetime.now(timezone.utc)), [])

    def test_run_request_makes_only_selected_task_due(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            notebook_dir = Path(temporary_directory)
            first = GardenerTask(
                "one",
                "Research",
                "One.md",
                "One",
                "Do one",
                60,
                last_started_at=datetime.now(timezone.utc).isoformat(),
            )
            second = GardenerTask("two", "Research", "Two.md", "Two", "Do two", 60)
            save_gardener_tasks(notebook_dir, [first, second])

            request_gardener_run(notebook_dir, {"two"})

            self.assertEqual([task.id for task in due_gardener_tasks(notebook_dir)], ["two"])

    def test_records_short_result_and_frequency_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            notebook_dir = Path(temporary_directory)
            task = GardenerTask("one", "Research", "One.md", "One", "Do one", 60)
            save_gardener_tasks(notebook_dir, [task])

            update_gardener_frequency(notebook_dir, "one", 15)
            mark_gardener_finished(notebook_dir, "one", status="success", result="word " * 100)

            updated = load_gardener_tasks(notebook_dir)[0]
            self.assertEqual(updated.frequency_minutes, 15)
            self.assertEqual(updated.last_status, "success")
            self.assertLessEqual(len(updated.last_result or ""), 240)

    def test_resets_interrupted_tasks_and_makes_them_due(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            notebook_dir = Path(temporary_directory)
            interrupted = GardenerTask(
                "one", "Research", "One.md", "Collect", "Collect data", 60,
                last_started_at=datetime.now(timezone.utc).isoformat(), last_status="busy",
            )
            completed = GardenerTask("two", "Research", "Two.md", "Keep", "Keep data", 60, last_status="success")
            save_gardener_tasks(notebook_dir, [interrupted, completed])

            self.assertEqual(reset_interrupted_gardener_tasks(notebook_dir), 1)

            reset, unchanged = load_gardener_tasks(notebook_dir)
            self.assertIsNone(reset.last_started_at)
            self.assertEqual(reset.last_status, "ready")
            self.assertEqual(unchanged.last_status, "success")
            self.assertEqual([task.id for task in due_gardener_tasks(notebook_dir)], ["one", "two"])

    def test_tracks_and_clears_cached_script_process_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            notebook_dir = Path(temporary_directory)
            task = GardenerTask("one", "Research", "One.md", "Collect", "Collect data", 60)
            save_gardener_tasks(notebook_dir, [task])

            mark_gardener_process_started(notebook_dir, task.id, 12345)
            self.assertEqual(load_gardener_tasks(notebook_dir)[0].active_pid, 12345)

            mark_gardener_finished(notebook_dir, task.id, status="success", result="Done")
            self.assertIsNone(load_gardener_tasks(notebook_dir)[0].active_pid)

    def test_checksum_invalidates_cached_script_when_action_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_dir = Path(temporary_directory)
            task = GardenerTask("abcdef123456", "Research", "One.md", "Collect", "Collect data", 60)
            script_path = project_dir / gardener_script_path(task)
            script_path.parent.mkdir()
            script_path.write_text(f"{gardener_hash_comment(task)}\n", encoding="utf-8")

            self.assertEqual(current_gardener_script(project_dir, task), script_path.resolve())

            changed = GardenerTask(
                **{**task.__dict__, "instructions": "Collect different data"}
            )
            self.assertIsNone(current_gardener_script(project_dir, changed))

    def test_script_prompt_and_machine_readable_result_contract(self) -> None:
        task = GardenerTask("abcdef123456", "Research", "One.md", "Collect", "Collect data", 60)

        prompt = build_gardener_script_prompt(task)
        result = parse_gardener_result(
            '{"robot": true, "status": "partial", "summary": "Needs review", "details": {"rows": 4}}'
        )
        followup = build_gardener_followup_prompt(task, result)

        self.assertIn(gardener_hash_comment(task), prompt)
        self.assertIn("Do not execute the action now", prompt)
        self.assertEqual(result, GardenerResult(True, "partial", "Needs review", {"rows": 4}))
        self.assertIn('"rows": 4', followup)

    def test_rejects_invalid_machine_readable_result(self) -> None:
        with self.assertRaisesRegex(ValueError, "robot"):
            parse_gardener_result('{"status": "ok", "summary": "Done"}')


if __name__ == "__main__":
    unittest.main()
