from __future__ import annotations

from datetime import date
import unittest
from pathlib import Path
from unittest.mock import patch

from lightacademia.chat import chat_log_path, list_chat_log_dates, read_chat_log


class ChatLogTest(unittest.TestCase):
    @patch("lightacademia.chat.Path.exists", return_value=True)
    @patch("lightacademia.chat.Path.glob")
    def test_lists_chat_logs_by_date(self, glob, exists) -> None:
        glob.return_value = [
            Path("/project/chats/2026-07-02.md"),
            Path("/project/chats/2026-06-30.md"),
            Path("/project/chats/not-a-date.md"),
        ]

        self.assertEqual(
            list_chat_log_dates(Path("/project")),
            [date(2026, 7, 2), date(2026, 6, 30)],
        )

    @patch("lightacademia.chat.Path.exists", return_value=True)
    @patch("lightacademia.chat.Path.read_text", return_value="newer")
    def test_reads_chat_log_by_date(self, read_text, exists) -> None:
        self.assertEqual(read_chat_log(Path("/project"), date(2026, 7, 2)), "newer")
        read_text.assert_called_once_with(encoding="utf-8")
        self.assertEqual(chat_log_path(Path("/project"), date(2026, 7, 2)), Path("/project/chats/2026-07-02.md"))

    @patch("lightacademia.chat.Path.exists", return_value=False)
    def test_missing_chat_log_reads_as_empty(self, exists) -> None:
        self.assertEqual(read_chat_log(Path("/project"), date(2026, 7, 2)), "")


if __name__ == "__main__":
    unittest.main()
