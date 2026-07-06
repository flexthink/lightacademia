from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from lightacademia.search import SearchUnavailable, make_snippet, search_notes
from lightacademia.storage import Note


class FakeBM25:
    indexed_tokens = None

    def index(self, tokens):
        FakeBM25.indexed_tokens = tokens

    def retrieve(self, query_tokens, k):
        return [[1, 0]], [[3.0, 1.2]]


class FakeBM25Module:
    BM25 = FakeBM25

    @staticmethod
    def tokenize(documents):
        return documents


class SearchNotesTest(unittest.TestCase):
    @patch("lightacademia.search.read_note")
    def test_searches_notes_with_bm25s(self, read_note) -> None:
        read_note.side_effect = ["paintbrush transformer notes", "grid search metrics"]
        notes = [
            Note("Model.md", Path("/project/Model.md")),
            Note("Results.md", Path("/project/Results.md")),
        ]

        results = search_notes(notes, "metrics", bm25s_module=FakeBM25Module())

        self.assertEqual([result.note.name for result in results], ["Results.md", "Model.md"])
        self.assertEqual(results[0].score, 3.0)
        self.assertIn("metrics", results[0].snippet)

    def test_empty_query_returns_no_results(self) -> None:
        self.assertEqual(search_notes([], "   ", bm25s_module=FakeBM25Module()), [])

    def test_missing_bm25s_reports_search_unavailable(self) -> None:
        with patch.dict("sys.modules", {"bm25s": None}):
            with self.assertRaises(SearchUnavailable):
                search_notes([Note("Home.md", Path("/project/Home.md"))], "home")

    def test_snippet_focuses_near_query_term(self) -> None:
        snippet = make_snippet("alpha " * 80 + "important result " + "omega " * 80, "important", width=80)

        self.assertIn("important result", snippet)
        self.assertTrue(snippet.startswith("..."))


if __name__ == "__main__":
    unittest.main()
