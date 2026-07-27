from __future__ import annotations

import unittest

from lightacademia.boards import (
    board_data_file,
    board_fetch_hash,
    board_fetch_hash_comment,
    board_fetch_script,
    build_board_action_prompt,
    build_board_prompt,
    parse_note_boards,
    script_fetch_hash,
)


BOARD_MARKDOWN = """```board
name: Experiments
actions:
- Resume: Resume the selected experiment
- Troubleshoot: Tail the log file and summarize the findings
filters:
- Name
- Cluster: dropdown
- Status: dropdown
columns:
- Name
- Cluster
- Status
- Epoch

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
        self.assertEqual(
            [(board_filter.column, board_filter.filter_type) for board_filter in board.filters],
            [("Name", "text"), ("Cluster", "dropdown"), ("Status", "dropdown")],
        )
        self.assertEqual(board.columns, ("Name", "Cluster", "Status", "Epoch"))
        self.assertIn("within the last week", board.instructions)

    def test_normalizes_board_data_filename(self) -> None:
        self.assertEqual(board_data_file("Recent Cluster Runs"), "data/board-recent-cluster-runs.csv")

    def test_reports_missing_separator_and_instructions(self) -> None:
        result = parse_note_boards("```board\nname: Broken\n```\n")

        self.assertEqual(result.boards, ())
        self.assertIn("blank line", result.errors[0].message)

    def test_reports_unsupported_filter_type_without_losing_board(self) -> None:
        result = parse_note_boards(
            "```board\nname: Runs\nfilters:\n- Status: checkbox\n\nFetch runs.\n```\n"
        )

        self.assertEqual(len(result.boards), 1)
        self.assertEqual(result.boards[0].filters, ())
        self.assertIn("unsupported type", result.errors[0].message)

    def test_builds_fast_fetch_script_instructions_with_hash(self) -> None:
        result = parse_note_boards(
            """```board
name: Recent Runs
fetch: fast
columns:
- Name
- Accuracy

Fetch recent runs.
```
"""
        )
        board = result.boards[0]

        self.assertEqual(board.fetch_mode, "fast")
        self.assertEqual(board_fetch_script(board.name), "code/board-recent-runs-fetch.py")
        marker = board_fetch_hash_comment(board)
        self.assertEqual(script_fetch_hash(f"#!/usr/bin/env python\n{marker}\n"), board_fetch_hash(board))
        prompt = build_board_prompt(board, "Runs.md")
        self.assertIn("Fast fetch implementation requirements:", prompt)
        self.assertIn("code/board-recent-runs-fetch.py", prompt)
        self.assertIn(marker, prompt)
        self.assertIn("LIGHTACADEMIA_TOOLS", prompt)
        self.assertIn("Never hardcode", prompt)
        self.assertIn("Run the script now", prompt)

        changed = parse_note_boards(
            """```board
name: Recent Runs
fetch: fast
columns:
- Name
- Accuracy

Fetch all recent runs.
```
"""
        ).boards[0]
        self.assertNotEqual(board_fetch_hash(board), board_fetch_hash(changed))

    def test_reports_unsupported_fetch_mode_and_uses_agent_fetch(self) -> None:
        result = parse_note_boards(
            "```board\nname: Runs\nfetch: instant\n\nFetch runs.\n```\n"
        )

        self.assertEqual(result.boards[0].fetch_mode, "agent")
        self.assertIn("fetch mode", result.errors[0].message)

    def test_builds_refresh_prompt_with_context(self) -> None:
        board = parse_note_boards(BOARD_MARKDOWN).boards[0]

        prompt = build_board_prompt(board, "Experiments.md")

        self.assertIn("Source note: Experiments.md", prompt)
        self.assertIn("Board name: Experiments", prompt)
        self.assertIn("Board data file: data/board-experiments.csv", prompt)
        self.assertIn("Fetch experiments", prompt)
        self.assertIn("Required CSV columns, in this order:", prompt)
        self.assertIn("- Name\n- Cluster\n- Status\n- Epoch", prompt)
        self.assertIn("use those exact column names and order", prompt)
        self.assertIn("Do not execute, simulate, or apply any board action", prompt)
        self.assertNotIn("Resume the selected experiment", prompt)
        self.assertNotIn("Tail the log file and summarize the findings", prompt)

    def test_reports_invalid_columns_without_losing_valid_columns(self) -> None:
        result = parse_note_boards(
            "```board\nname: Runs\ncolumns:\n- Name\n- 42\n- name\n\nFetch runs.\n```\n"
        )

        self.assertEqual(result.boards[0].columns, ("Name",))
        self.assertEqual(len(result.errors), 2)

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
