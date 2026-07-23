from __future__ import annotations

import unittest

from lightacademia.boards import (
    board_data_file,
    build_board_action_prompt,
    build_board_prompt,
    parse_note_boards,
)


BOARD_MARKDOWN = """```board
name: Experiments
actions:
- Resume: Resume the selected experiment
- Troubleshoot: Tail the log file and summarize the findings

Fetch experiments from the cluster that have run within the last week.
```
"""


class ParseNoteBoardsTest(unittest.TestCase):
    def test_parses_board_metadata_actions_and_fetch_instructions(self) -> None:
        result = parse_note_boards(BOARD_MARKDOWN)

        self.assertEqual(result.errors, ())
        self.assertEqual(len(result.boards), 1)
        board = result.boards[0]
        self.assertEqual(board.name, "Experiments")
        self.assertEqual(board.data_file, "data/board-experiments.csv")
        self.assertEqual([action.name for action in board.actions], ["Resume", "Troubleshoot"])
        self.assertIn("within the last week", board.instructions)

    def test_normalizes_board_data_filename(self) -> None:
        self.assertEqual(board_data_file("Recent Cluster Runs"), "data/board-recent-cluster-runs.csv")

    def test_reports_missing_separator_and_instructions(self) -> None:
        result = parse_note_boards("```board\nname: Broken\n```\n")

        self.assertEqual(result.boards, ())
        self.assertIn("blank line", result.errors[0].message)

    def test_builds_refresh_prompt_with_context(self) -> None:
        board = parse_note_boards(BOARD_MARKDOWN).boards[0]

        prompt = build_board_prompt(board, "Experiments.md")

        self.assertIn("Source note: Experiments.md", prompt)
        self.assertIn("Board name: Experiments", prompt)
        self.assertIn("Board data file: data/board-experiments.csv", prompt)
        self.assertIn("Fetch experiments", prompt)
        self.assertIn("Do not execute, simulate, or apply any board action", prompt)
        self.assertNotIn("Resume the selected experiment", prompt)
        self.assertNotIn("Tail the log file and summarize the findings", prompt)

    def test_builds_row_action_prompt_with_selected_row(self) -> None:
        board = parse_note_boards(BOARD_MARKDOWN).boards[0]

        prompt = build_board_action_prompt(
            board,
            board.actions[1],
            {"experiment": "run-42", "epoch": 8},
            "Experiments.md",
        )

        self.assertIn("Source note: Experiments.md", prompt)
        self.assertIn("Board name: Experiments", prompt)
        self.assertIn("Action name: Troubleshoot", prompt)
        self.assertIn('"experiment": "run-42"', prompt)
        self.assertIn('"epoch": 8', prompt)


if __name__ == "__main__":
    unittest.main()
