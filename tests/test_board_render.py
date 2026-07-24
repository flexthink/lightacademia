from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest


class BoardRenderTest(unittest.TestCase):
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

            app_test.text_input[0].input("run-2").run()

            self.assertEqual(list(app_test.exception), [])
            self.assertEqual(app_test.dataframe[0].value["Name"].tolist(), ["run-2"])


if __name__ == "__main__":
    unittest.main()
