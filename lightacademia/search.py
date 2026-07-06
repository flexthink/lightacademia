from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .storage import Note, read_note


class SearchUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class SearchResult:
    note: Note
    score: float
    snippet: str


def search_notes(notes: list[Note], query: str, limit: int = 5, bm25s_module: Any | None = None) -> list[SearchResult]:
    cleaned_query = query.strip()
    if not cleaned_query or not notes:
        return []

    bm25s = bm25s_module
    if bm25s is None:
        try:
            import bm25s as bm25s  # type: ignore[no-redef]
        except ImportError as exc:
            raise SearchUnavailable("Install `bm25s` to enable search.") from exc

    documents = [f"{note.name}\n{read_note(note)}" for note in notes]
    corpus_tokens = bm25s.tokenize(documents)
    retriever = bm25s.BM25()
    retriever.index(corpus_tokens)
    query_tokens = bm25s.tokenize([cleaned_query])
    results, scores = retriever.retrieve(query_tokens, k=min(limit, len(notes)))

    ranked: list[SearchResult] = []
    for index, score in zip(_flatten(results), _flatten(scores), strict=False):
        note_index = int(index)
        if note_index < 0 or note_index >= len(notes):
            continue
        numeric_score = float(score)
        if numeric_score <= 0:
            continue
        ranked.append(
            SearchResult(
                note=notes[note_index],
                score=numeric_score,
                snippet=make_snippet(documents[note_index], cleaned_query),
            )
        )
    return ranked


def make_snippet(document: str, query: str, width: int = 180) -> str:
    collapsed = " ".join(document.split())
    if len(collapsed) <= width:
        return collapsed

    terms = [term.lower() for term in query.split() if term.strip()]
    lower = collapsed.lower()
    start = 0
    for term in terms:
        found = lower.find(term)
        if found >= 0:
            start = max(0, found - width // 3)
            break
    end = min(len(collapsed), start + width)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(collapsed) else ""
    return f"{prefix}{collapsed[start:end]}{suffix}"


def _flatten(values: Any) -> list[Any]:
    if hasattr(values, "tolist"):
        values = values.tolist()
    if isinstance(values, tuple):
        values = list(values)
    if isinstance(values, list):
        if len(values) == 1 and isinstance(values[0], list):
            return values[0]
        return values
    return [values]
