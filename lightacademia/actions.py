from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from markdown_it import MarkdownIt


@dataclass(frozen=True)
class NoteAction:
    name: str
    instructions: str
    line: int
    end_line: int


@dataclass(frozen=True)
class ActionParseError:
    line: int
    message: str


@dataclass(frozen=True)
class ActionParseResult:
    actions: tuple[NoteAction, ...]
    errors: tuple[ActionParseError, ...]


_MARKDOWN = MarkdownIt("commonmark")


def format_note_action(name: str, instructions: str) -> str:
    cleaned_name = name.strip()
    cleaned_instructions = instructions.strip()
    longest_backtick_run = max(
        (len(run) for run in _backtick_runs(f"{cleaned_name}\n{cleaned_instructions}")),
        default=0,
    )
    fence = "`" * max(3, longest_backtick_run + 1)
    return f"{fence}action\nname: {cleaned_name}\n\n{cleaned_instructions}\n{fence}"


def _backtick_runs(value: str) -> Iterator[str]:
    run = ""
    for character in value:
        if character == "`":
            run += character
        elif run:
            yield run
            run = ""
    if run:
        yield run


def parse_note_actions(markdown: str) -> ActionParseResult:
    actions: list[NoteAction] = []
    errors: list[ActionParseError] = []

    for token in _MARKDOWN.parse(markdown):
        if token.type != "fence" or token.info.strip() != "action":
            continue

        block_line = token.map[0] + 1 if token.map else 1
        lines = token.content.splitlines()
        first_content = next((index for index, line in enumerate(lines) if line.strip()), None)
        if first_content is None:
            errors.append(ActionParseError(block_line, "Action block is empty."))
            continue

        header = lines[first_content].strip()
        if not header.startswith("name:"):
            errors.append(ActionParseError(block_line, "First non-empty line must be `name: ...`."))
            continue

        name = header.removeprefix("name:").strip()
        if not name:
            errors.append(ActionParseError(block_line, "Action name cannot be empty."))
            continue

        separator = first_content + 1
        if separator >= len(lines) or lines[separator].strip():
            errors.append(ActionParseError(block_line, "Add a blank line after the action name."))
            continue

        instructions = "\n".join(lines[separator + 1 :]).strip()
        if not instructions:
            errors.append(ActionParseError(block_line, "Action instructions cannot be empty."))
            continue

        end_line = token.map[1] if token.map else block_line
        actions.append(NoteAction(name=name, instructions=instructions, line=block_line, end_line=end_line))

    return ActionParseResult(tuple(actions), tuple(errors))


def build_action_prompt(action: NoteAction, note_name: str, user_prompt: str = "") -> str:
    prompt = (
        "Selected note action:\n"
        f"Source note: {note_name}\n"
        f"Action name: {action.name}\n\n"
        "Instructions:\n"
        f"{action.instructions}"
    )
    if user_prompt.strip():
        prompt += f"\n\nAdditional user request:\n{user_prompt.strip()}"
    return prompt
