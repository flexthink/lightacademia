from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

from lightacademia.boards import (
    board_action_hash_comment,
    board_action_script,
    board_fetch_hash_comment,
    parse_note_boards,
)


class BoardRenderTest(unittest.TestCase):
    def test_current_fast_action_runs_without_agent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_dir = Path(temporary_directory)
            (project_dir / "code").mkdir()
            (project_dir / "data").mkdir()
            board = parse_note_boards(
                """```board
name: Experiments
actions:
- [fast] Resume: Resume the selected experiment

Fetch experiments.
```
"""
            ).boards[0]
            script_path = project_dir / board_action_script(board.name, board.actions[0].name)
            script_path.write_text(
                f"""\
{board_action_hash_comment(board)}
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--row-json", required=True)
parser.add_argument("--source-note", required=True)
parser.add_argument("--board-data-file", required=True)
args = parser.parse_args()
Path("data/action.json").write_text(json.dumps({{
    "row": json.loads(args.row_json),
    "source_note": args.source_note,
    "board_data_file": args.board_data_file,
}}))
""",
                encoding="utf-8",
            )
            script = f"""\
from pathlib import Path
from app import current_fast_action_script, run_fast_board_action
from lightacademia.boards import parse_note_boards

board = parse_note_boards({'''```board
name: Experiments
actions:
- [fast] Resume: Resume the selected experiment

Fetch experiments.
```'''!r}).boards[0]
script_path = current_fast_action_script(board, board.actions[0], Path({str(project_dir)!r}))
assert script_path is not None
run_fast_board_action(
    board,
    board.actions[0],
    {{"Name": "run-42", "Cluster": "Fir"}},
    Path({str(project_dir)!r}),
    "Experiments.md",
    script_path,
)
"""

            app_test = AppTest.from_string(script).run()

            self.assertEqual(list(app_test.exception), [])
            result = json.loads((project_dir / "data" / "action.json").read_text(encoding="utf-8"))
            self.assertEqual(result["row"], {"Name": "run-42", "Cluster": "Fir"})
            self.assertEqual(result["source_note"], "Experiments.md")
            self.assertEqual(result["board_data_file"], "data/board-experiments.csv")

    def test_renders_board_dataframe_with_action_column(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_dir = Path(temporary_directory)
            data_dir = project_dir / "data"
            data_dir.mkdir()
            (data_dir / "board-experiments.csv").write_text(
                "Name,Cluster,Status,Epoch\nrun-1,Fir,Running,4\nrun-2,Rorqual,Failed,7\n",
                encoding="utf-8",
            )
            script = f'''\
from pathlib import Path
from app import render_note_board
from lightacademia.boards import parse_note_boards

board = parse_note_boards("""```board
name: Experiments
actions:
- Resume: Resume the selected experiment
filters:
- Name
- Cluster: dropdown
- Status: dropdown
columns:
- Name
- Status
- Cluster
- Epoch

Fetch recent experiments.
```
""").boards[0]
render_note_board(board, Path({str(project_dir)!r}), "Experiments.md", "test", 0, True)
'''

            app_test = AppTest.from_string(script).run()

            self.assertEqual(list(app_test.exception), [])
            self.assertEqual(len(app_test.dataframe), 1)
            self.assertEqual(len(app_test.text_input), 1)
            self.assertEqual(len(app_test.selectbox), 2)
            self.assertEqual(
                app_test.dataframe[0].value["__board_action_0"].tolist(),
                [":material/bolt: Resume", ":material/bolt: Resume"],
            )

            app_test.text_input[0].input("run-2").run()

            self.assertEqual(list(app_test.exception), [])
            self.assertEqual(app_test.dataframe[0].value["Name"].tolist(), ["run-2"])

    def test_matching_fast_fetch_script_runs_without_agent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_dir = Path(temporary_directory)
            (project_dir / "data").mkdir()
            (project_dir / "code").mkdir()
            csv_path = project_dir / "data" / "board-demo-fetch.csv"
            csv_path.write_text("Name,Accuracy\nold,0.50\n", encoding="utf-8")
            board_markdown = """```board
name: Demo Fetch
fetch: fast
columns:
- Name
- Accuracy

Generate demo rows.
```
"""
            board = parse_note_boards(board_markdown).boards[0]
            script_path = project_dir / "code" / "board-demo-fetch-fetch.py"
            script_path.write_text(
                "\n".join(
                    [
                        "import os",
                        "from pathlib import Path",
                        board_fetch_hash_comment(board),
                        'assert os.environ["LIGHTACADEMIA_TOOLS"]',
                        'Path("data/board-demo-fetch.csv").write_text(',
                        '    "Name,Accuracy\\nnew,0.99\\n", encoding="utf-8"',
                        ")",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            script = f'''\
from pathlib import Path
from app import render_note_board
from lightacademia.boards import parse_note_boards

board = parse_note_boards({board_markdown!r}).boards[0]
render_note_board(board, Path({str(project_dir)!r}), "Demo.md", "fast-test", 0, True)
'''

            app_test = AppTest.from_string(script).run()
            app_test.button[0].click().run()

            self.assertEqual(list(app_test.exception), [])
            self.assertEqual(csv_path.read_text(encoding="utf-8"), "Name,Accuracy\nnew,0.99\n")


if __name__ == "__main__":
    unittest.main()
