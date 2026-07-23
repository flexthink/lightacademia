from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass

import yaml
from markdown_it import MarkdownIt


@dataclass(frozen=True)
class BoardAction:
    name: str
    instructions: str


@dataclass(frozen=True)
class NoteBoard:
    name: str
    instructions: str
    actions: tuple[BoardAction, ...]
    data_file: str
    line: int
    end_line: int


@dataclass(frozen=True)
class BoardParseError:
    line: int
    message: str


@dataclass(frozen=True)
class BoardParseResult:
    boards: tuple[NoteBoard, ...]
    errors: tuple[BoardParseError, ...]


_MARKDOWN = MarkdownIt("commonmark")


def normalize_board_name(name: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "-", name.strip().lower()).strip("-")
    return normalized or "board"


def board_data_file(name: str) -> str:
    return f"data/board-{normalize_board_name(name)}.csv"


def parse_note_boards(markdown: str) -> BoardParseResult:
    boards: list[NoteBoard] = []
    errors: list[BoardParseError] = []

    for token in _MARKDOWN.parse(markdown):
        if token.type != "fence" or token.info.strip() != "board":
            continue

        block_line = token.map[0] + 1 if token.map else 1
        lines = token.content.splitlines()
        first_content = next((index for index, line in enumerate(lines) if line.strip()), None)
        if first_content is None:
            errors.append(BoardParseError(block_line, "Board block is empty."))
            continue

        separator = next(
            (index for index in range(first_content + 1, len(lines)) if not lines[index].strip()),
            None,
        )
        if separator is None:
            errors.append(BoardParseError(block_line, "Add a blank line before the board fetch instructions."))
            continue

        try:
            metadata = yaml.safe_load("\n".join(lines[first_content:separator]))
        except yaml.YAMLError as exc:
            errors.append(BoardParseError(block_line, f"Could not parse board metadata: {exc}"))
            continue
        if not isinstance(metadata, dict):
            errors.append(BoardParseError(block_line, "Board metadata must be a YAML mapping."))
            continue

        name = str(metadata.get("name") or "").strip()
        if not name:
            errors.append(BoardParseError(block_line, "Board name cannot be empty."))
            continue

        instructions = "\n".join(lines[separator + 1 :]).strip()
        if not instructions:
            errors.append(BoardParseError(block_line, "Board fetch instructions cannot be empty."))
            continue

        parsed_actions: list[BoardAction] = []
        raw_actions = metadata.get("actions", [])
        if raw_actions is None:
            raw_actions = []
        if not isinstance(raw_actions, list):
            errors.append(BoardParseError(block_line, "Board actions must be a YAML list."))
            raw_actions = []
        for raw_action in raw_actions:
            if not isinstance(raw_action, dict) or len(raw_action) != 1:
                errors.append(BoardParseError(block_line, "Each board action must contain one name and instruction."))
                continue
            action_name, action_instructions = next(iter(raw_action.items()))
            cleaned_name = str(action_name).strip()
            cleaned_instructions = str(action_instructions or "").strip()
            if not cleaned_name or not cleaned_instructions:
                errors.append(BoardParseError(block_line, "Board action names and instructions cannot be empty."))
                continue
            parsed_actions.append(BoardAction(cleaned_name, cleaned_instructions))

        end_line = token.map[1] if token.map else block_line
        boards.append(
            NoteBoard(
                name=name,
                instructions=instructions,
                actions=tuple(parsed_actions),
                data_file=board_data_file(name),
                line=block_line,
                end_line=end_line,
            )
        )

    return BoardParseResult(tuple(boards), tuple(errors))


def build_board_prompt(board: NoteBoard, note_name: str) -> str:
    return (
        "Selected note board refresh:\n"
        f"Source note: {note_name}\n"
        f"Board name: {board.name}\n"
        f"Board data file: {board.data_file}\n\n"
        "Fetch instructions:\n"
        f"{board.instructions}\n\n"
        "Board action constraint:\n"
        "- This is a data refresh, not a board action.\n"
        "- Ignore the board's actions metadata, even if you read it from the source note.\n"
        "- Do not execute, simulate, or apply any board action to any row. Board actions are "
        "separate commands that run only when the user presses a row action button.\n\n"
        "Output requirements:\n"
        f"- Fetch the board data using the instructions above.\n"
        f"- Write or replace the board CSV at `{board.data_file}` inside the selected project.\n"
        "- Include a header row and one data record per board row.\n"
        "- Do not modify the board block unless the user asks you to."
    )


def build_board_action_prompt(
    board: NoteBoard,
    action: BoardAction,
    row: Mapping[str, object],
    note_name: str,
) -> str:
    row_json = json.dumps(dict(row), ensure_ascii=False, indent=2, default=str)
    return (
        "Selected board row action:\n"
        f"Source note: {note_name}\n"
        f"Board name: {board.name}\n"
        f"Board data file: {board.data_file}\n"
        f"Action name: {action.name}\n\n"
        "Selected row:\n"
        f"```json\n{row_json}\n```\n\n"
        "Action instructions:\n"
        f"{action.instructions}"
    )
