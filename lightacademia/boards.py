from __future__ import annotations

import hashlib
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
    fast: bool = False
    refresh: bool = False


@dataclass(frozen=True)
class BoardFilter:
    column: str
    filter_type: str


@dataclass(frozen=True)
class NoteBoard:
    name: str
    instructions: str
    actions: tuple[BoardAction, ...]
    filters: tuple[BoardFilter, ...]
    columns: tuple[str, ...]
    fetch_mode: str
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
FETCH_HASH_MARKER = "lightacademia-board-fetch-sha256"
ACTION_HASH_MARKER = "lightacademia-board-action-sha256"


def normalize_board_name(name: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "-", name.strip().lower()).strip("-")
    return normalized or "board"


def board_data_file(name: str) -> str:
    return f"data/board-{normalize_board_name(name)}.csv"


def board_fetch_script(name: str) -> str:
    return f"code/board-{normalize_board_name(name)}-fetch.py"


def board_action_script(board_name: str, action_name: str) -> str:
    return (
        f"code/board-{normalize_board_name(board_name)}"
        f"-action-{normalize_board_name(action_name)}.py"
    )


def board_definition_hash(board: NoteBoard) -> str:
    board_spec = {
        "name": board.name,
        "instructions": board.instructions,
        "actions": [
            {
                "name": action.name,
                "instructions": action.instructions,
                "fast": action.fast,
                "refresh": action.refresh,
            }
            for action in board.actions
        ],
        "filters": [
            {"column": board_filter.column, "type": board_filter.filter_type}
            for board_filter in board.filters
        ],
        "columns": list(board.columns),
        "fetch_mode": board.fetch_mode,
        "data_file": board.data_file,
    }
    encoded = json.dumps(board_spec, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def board_fetch_hash(board: NoteBoard) -> str:
    return board_definition_hash(board)


def board_fetch_hash_comment(board: NoteBoard) -> str:
    return f"# {FETCH_HASH_MARKER}: {board_fetch_hash(board)}"


def board_action_hash_comment(board: NoteBoard) -> str:
    return f"# {ACTION_HASH_MARKER}: {board_definition_hash(board)}"


def script_fetch_hash(script: str) -> str | None:
    match = re.search(
        rf"(?m)^\s*#\s*{re.escape(FETCH_HASH_MARKER)}:\s*([0-9a-fA-F]{{64}})\s*$",
        script,
    )
    return match.group(1).lower() if match else None


def script_action_hash(script: str) -> str | None:
    match = re.search(
        rf"(?m)^\s*#\s*{re.escape(ACTION_HASH_MARKER)}:\s*([0-9a-fA-F]{{64}})\s*$",
        script,
    )
    return match.group(1).lower() if match else None


def normalize_action_flags_yaml(source: str) -> str:
    return re.sub(
        r"(?im)^(\s*-\s*)(\[[A-Za-z][A-Za-z\s,]*\]\s+[^:\n]+)(\s*:)",
        lambda match: f"{match.group(1)}{json.dumps(match.group(2))}{match.group(3)}",
        source,
    )


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
            metadata_source = "\n".join(lines[first_content:separator])
            metadata = yaml.safe_load(normalize_action_flags_yaml(metadata_source))
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

        fetch_mode = str(metadata.get("fetch") or "agent").strip().lower()
        if fetch_mode not in {"agent", "fast"}:
            errors.append(
                BoardParseError(
                    block_line,
                    f"Board fetch mode `{fetch_mode}` is unsupported; use `fast` or omit it.",
                )
            )
            fetch_mode = "agent"

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
            fast = False
            refresh = False
            flags_match = re.match(r"^\[([^\]]+)\]\s+", cleaned_name)
            if flags_match:
                flags = {flag.strip().casefold() for flag in flags_match.group(1).split(",")}
                unsupported_flags = flags - {"fast", "refresh"}
                if unsupported_flags:
                    errors.append(
                        BoardParseError(
                            block_line,
                            "Unsupported board action flag(s): "
                            f"{', '.join(sorted(unsupported_flags))}. Use `fast` and/or `refresh`.",
                        )
                    )
                fast = "fast" in flags
                refresh = "refresh" in flags
                cleaned_name = cleaned_name[flags_match.end() :].strip()
            if not cleaned_name or not cleaned_instructions:
                errors.append(BoardParseError(block_line, "Board action names and instructions cannot be empty."))
                continue
            parsed_actions.append(BoardAction(cleaned_name, cleaned_instructions, fast, refresh))

        parsed_filters: list[BoardFilter] = []
        raw_filters = metadata.get("filters", [])
        if raw_filters is None:
            raw_filters = []
        if not isinstance(raw_filters, list):
            errors.append(BoardParseError(block_line, "Board filters must be a YAML list."))
            raw_filters = []
        seen_filter_columns: set[str] = set()
        for raw_filter in raw_filters:
            if isinstance(raw_filter, str):
                filter_column = raw_filter.strip()
                filter_type = "text"
            elif isinstance(raw_filter, dict) and len(raw_filter) == 1:
                raw_column, raw_type = next(iter(raw_filter.items()))
                filter_column = str(raw_column).strip()
                filter_type = str(raw_type or "text").strip().lower()
            else:
                errors.append(
                    BoardParseError(
                        block_line,
                        "Each board filter must be a column name or one column-to-type mapping.",
                    )
                )
                continue
            if not filter_column:
                errors.append(BoardParseError(block_line, "Board filter column names cannot be empty."))
                continue
            if filter_type not in {"text", "dropdown"}:
                errors.append(
                    BoardParseError(
                        block_line,
                        f"Board filter `{filter_column}` has unsupported type `{filter_type}`; "
                        "use `text` or `dropdown`.",
                    )
                )
                continue
            normalized_column = filter_column.casefold()
            if normalized_column in seen_filter_columns:
                errors.append(BoardParseError(block_line, f"Board filter `{filter_column}` is duplicated."))
                continue
            seen_filter_columns.add(normalized_column)
            parsed_filters.append(BoardFilter(filter_column, filter_type))

        parsed_columns: list[str] = []
        raw_columns = metadata.get("columns", [])
        if raw_columns is None:
            raw_columns = []
        if not isinstance(raw_columns, list):
            errors.append(BoardParseError(block_line, "Board columns must be a YAML list."))
            raw_columns = []
        seen_columns: set[str] = set()
        for raw_column in raw_columns:
            if not isinstance(raw_column, str) or not raw_column.strip():
                errors.append(BoardParseError(block_line, "Each board column must be a non-empty name."))
                continue
            column = raw_column.strip()
            normalized_column = column.casefold()
            if normalized_column in seen_columns:
                errors.append(BoardParseError(block_line, f"Board column `{column}` is duplicated."))
                continue
            seen_columns.add(normalized_column)
            parsed_columns.append(column)

        end_line = token.map[1] if token.map else block_line
        boards.append(
            NoteBoard(
                name=name,
                instructions=instructions,
                actions=tuple(parsed_actions),
                filters=tuple(parsed_filters),
                columns=tuple(parsed_columns),
                fetch_mode=fetch_mode,
                data_file=board_data_file(name),
                line=block_line,
                end_line=end_line,
            )
        )

    return BoardParseResult(tuple(boards), tuple(errors))


def fast_action_maintenance_requirements(board: NoteBoard) -> str:
    fast_actions = [action for action in board.actions if action.fast]
    if not fast_actions:
        return ""
    scripts = "\n".join(
        (
            f"- `{board_action_script(board.name, action.name)}` for `{action.name}`: "
            f"{action.instructions}"
        )
        for action in fast_actions
    )
    return (
        "Fast action script maintenance requirements:\n"
        "- Check every fast action script listed below. Create it if missing, or update it "
        "if its marked board hash does not match.\n"
        f"{scripts}\n"
        "- Include this exact marked comment in every fast action script:\n"
        f"  `{board_action_hash_comment(board)}`\n"
        "- Each script must run non-interactively with Python from the project root and accept:\n"
        "  `--row-json <json> --source-note <name> --board-data-file <path>`\n"
        "- Parse `--row-json` as a JSON object and use the relevant board column values as "
        "action parameters.\n"
        "- Reuse the project's SKILL.md and researcher tools where appropriate.\n"
        "- Locate researcher tools through the `LIGHTACADEMIA_TOOLS` environment variable "
        "at runtime. Never hardcode the current tools directory into a script.\n"
    )


def build_board_prompt(board: NoteBoard, note_name: str) -> str:
    column_requirements = ""
    if board.columns:
        requested_columns = "\n".join(f"- {column}" for column in board.columns)
        column_requirements = (
            "Required CSV columns, in this order:\n"
            f"{requested_columns}\n\n"
        )
    fast_fetch_requirements = ""
    if board.fetch_mode == "fast":
        fast_fetch_requirements = (
            "Fast fetch implementation requirements:\n"
            f"- Create or update `{board_fetch_script(board.name)}` inside the selected project.\n"
            "- Include this exact marked comment in the script:\n"
            f"  `{board_fetch_hash_comment(board)}`\n"
            "- Implement the fetch instructions in that script. It must run non-interactively "
            "with Python from the project root and write the board CSV at the exact path above.\n"
            "- Reuse the project's SKILL.md and researcher tools where appropriate.\n"
            "- Locate researcher tools through the `LIGHTACADEMIA_TOOLS` environment variable "
            "at runtime. Never hardcode the current tools directory into the script.\n"
            "- Run the script now to populate the CSV, and fix the script if that run fails.\n\n"
        )
    fast_action_requirements = fast_action_maintenance_requirements(board)
    if fast_action_requirements:
        fast_action_requirements += (
            "- This refresh may create or update fast action scripts, but it must not execute "
            "any board action.\n\n"
        )
    return (
        "Selected note board refresh:\n"
        f"Source note: {note_name}\n"
        f"Board name: {board.name}\n"
        f"Board data file: {board.data_file}\n\n"
        "Fetch instructions:\n"
        f"{board.instructions}\n\n"
        "Board action constraint:\n"
        "- This is a data refresh, not a board action.\n"
        "- Do not perform board actions. Fast action definitions may be used only to create "
        "or update their reusable scripts.\n"
        "- Do not execute, simulate, or apply any board action to any row. Board actions are "
        "separate commands that run only when the user presses a row action button.\n\n"
        f"{column_requirements}"
        f"{fast_fetch_requirements}"
        f"{fast_action_requirements}"
        "Output requirements:\n"
        f"- Fetch the board data using the instructions above.\n"
        f"- Write or replace the board CSV at `{board.data_file}` inside the selected project.\n"
        "- Include a header row and one data record per board row.\n"
        "- When required CSV columns are listed above, use those exact column names and order.\n"
        "- Do not modify the board block unless the user asks you to."
    )


def build_board_action_prompt(
    board: NoteBoard,
    action: BoardAction,
    row: Mapping[str, object],
    note_name: str,
) -> str:
    row_json = json.dumps(dict(row), ensure_ascii=False, indent=2, default=str)
    maintenance_requirements = fast_action_maintenance_requirements(board)
    if maintenance_requirements:
        if action.fast:
            maintenance_requirements += (
                f"- After the scripts are ready, run `{board_action_script(board.name, action.name)}` "
                "for the selected row using the interface above. This first Robot-assisted run "
                "must perform the requested action as well as preparing the reusable script.\n\n"
            )
        else:
            maintenance_requirements += (
                "- Do not execute any fast action script during this command; the selected action "
                "below is not marked fast.\n\n"
            )
    refresh_requirements = ""
    if action.refresh:
        refresh_requirements = (
            "Refresh after action:\n"
            f"- After the action succeeds, refresh `{board.data_file}` using these board fetch instructions:\n"
            f"{board.instructions}\n"
            "- If this board uses fast fetch, create or update its marked fetch script when needed, "
            "then run it. Otherwise fetch the CSV directly.\n"
            "- Do not skip the refresh merely because the action itself completed successfully.\n\n"
        )
    return (
        "Selected board row action:\n"
        f"Source note: {note_name}\n"
        f"Board name: {board.name}\n"
        f"Board data file: {board.data_file}\n"
        f"Action name: {action.name}\n\n"
        "Selected row:\n"
        f"```json\n{row_json}\n```\n\n"
        f"{maintenance_requirements}"
        f"{refresh_requirements}"
        "Action instructions:\n"
        f"{action.instructions}"
    )
