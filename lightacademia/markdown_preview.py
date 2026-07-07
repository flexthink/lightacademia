from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlencode, urlsplit

from markdown_it import MarkdownIt
from tabulate import tabulate


SUPPORTED_IMAGE_SUFFIXES = {".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"}


class ProjectImageError(ValueError):
    pass


class ProjectNoteLinkError(ValueError):
    pass


@dataclass(frozen=True)
class MarkdownImage:
    start_line: int
    end_line: int
    target: str
    alt: str


@dataclass(frozen=True)
class MarkdownNoteLink:
    target: str
    label: str


@dataclass(frozen=True)
class MarkdownTable:
    start_line: int
    end_line: int
    source: str
    copy_text: str
    latex_text: str


_MARKDOWN = MarkdownIt("commonmark")


def find_local_note_links(markdown: str) -> tuple[MarkdownNoteLink, ...]:
    links: list[MarkdownNoteLink] = []
    for token in _MARKDOWN.parse(markdown):
        if token.type != "inline":
            continue
        children = token.children or []
        for index, child in enumerate(children):
            if child.type != "link_open":
                continue
            target = child.attrGet("href") or ""
            if not is_local_note_target(target):
                continue
            label_parts: list[str] = []
            for label_token in children[index + 1 :]:
                if label_token.type == "link_close":
                    break
                label_parts.append(label_token.content)
            links.append(MarkdownNoteLink(target=target, label="".join(label_parts)))
    return tuple(links)


def is_local_note_target(target: str) -> bool:
    parsed = urlsplit(target)
    return (
        bool(parsed.path)
        and not parsed.scheme
        and not parsed.netloc
        and Path(unquote(parsed.path)).suffix.lower() == ".md"
    )


def resolve_project_note(project_dir: Path, target: str) -> str:
    parsed = urlsplit(target)
    relative_path = Path(unquote(parsed.path).removeprefix("./"))
    if parsed.scheme or parsed.netloc or relative_path.is_absolute():
        raise ProjectNoteLinkError("Note link must be relative to the project.")
    if len(relative_path.parts) != 1 or relative_path.suffix.lower() != ".md":
        raise ProjectNoteLinkError("Note links must target a root-level Markdown note.")
    note_path = (project_dir / relative_path).resolve()
    if note_path.parent != project_dir.resolve() or not note_path.is_file():
        raise ProjectNoteLinkError(f"Note not found: `{relative_path}`.")
    return relative_path.name


def rewrite_project_note_links(markdown: str, project_dir: Path) -> tuple[str, tuple[str, ...]]:
    replacements: dict[str, str] = {}
    errors: list[str] = []
    for link in find_local_note_links(markdown):
        if link.target in replacements:
            continue
        try:
            note_name = resolve_project_note(project_dir, link.target)
        except ProjectNoteLinkError as exc:
            errors.append(str(exc))
            continue
        query = urlencode({"project": project_dir.name, "note": note_name})
        replacements[link.target] = f"?{query}"

    if not replacements:
        return markdown, tuple(dict.fromkeys(errors))

    protected_lines: set[int] = set()
    for token in _MARKDOWN.parse(markdown):
        if token.type in {"fence", "code_block"} and token.map:
            protected_lines.update(range(token.map[0], token.map[1]))

    rewritten_lines = []
    for line_number, line in enumerate(markdown.splitlines(keepends=True)):
        if line_number in protected_lines:
            rewritten_lines.append(line)
        else:
            rewritten_lines.append(_rewrite_note_links_outside_code(line, replacements))
    return "".join(rewritten_lines), tuple(dict.fromkeys(errors))


def _rewrite_note_links_outside_code(line: str, replacements: dict[str, str]) -> str:
    output: list[str] = []
    cursor = 0
    while cursor < len(line):
        opening = line.find("[", cursor)
        code_opening = line.find("`", cursor)
        if code_opening >= 0 and (opening < 0 or code_opening < opening):
            output.append(line[cursor:code_opening])
            run_end = code_opening
            while run_end < len(line) and line[run_end] == "`":
                run_end += 1
            marker = line[code_opening:run_end]
            closing = line.find(marker, run_end)
            if closing >= 0:
                output.append(line[code_opening : closing + len(marker)])
                cursor = closing + len(marker)
                continue
            output.append(line[code_opening:])
            break

        if opening < 0:
            output.append(line[cursor:])
            break
        output.append(line[cursor:opening])
        if opening > 0 and line[opening - 1] in {"!", "\\"}:
            output.append("[")
            cursor = opening + 1
            continue
        label_end = line.find("](", opening + 1)
        target_end = line.find(")", label_end + 2) if label_end >= 0 else -1
        if label_end < 0 or target_end < 0:
            output.append(line[opening:])
            break
        target = line[label_end + 2 : target_end].strip()
        replacement = replacements.get(target)
        if replacement is None:
            output.append(line[opening : target_end + 1])
        else:
            label = line[opening + 1 : label_end]
            output.append(f"[{label}]({replacement})")
        cursor = target_end + 1
    return "".join(output)


def find_standalone_images(markdown: str) -> tuple[MarkdownImage, ...]:
    tokens = _MARKDOWN.parse(markdown)
    images: list[MarkdownImage] = []

    for index in range(len(tokens) - 2):
        opening, inline, closing = tokens[index : index + 3]
        if (
            opening.type != "paragraph_open"
            or inline.type != "inline"
            or closing.type != "paragraph_close"
            or opening.map is None
            or opening.level != 0
        ):
            continue

        children = [
            child
            for child in (inline.children or [])
            if child.type != "text" or child.content.strip()
        ]
        if len(children) != 1 or children[0].type != "image":
            continue

        image = children[0]
        target = image.attrGet("src") or ""
        if not is_local_image_target(target):
            continue
        images.append(
            MarkdownImage(
                start_line=opening.map[0],
                end_line=opening.map[1],
                target=target,
                alt=image.content.strip(),
            )
        )

    return tuple(images)


def is_local_image_target(target: str) -> bool:
    parsed = urlsplit(target)
    return bool(parsed.path) and not parsed.scheme and not parsed.netloc


def resolve_project_image(project_dir: Path, target: str) -> Path:
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or not parsed.path:
        raise ProjectImageError("Image link must be a project-relative path.")

    relative_path = Path(unquote(parsed.path))
    if relative_path.is_absolute():
        raise ProjectImageError("Image link must be relative to the project.")

    assets_dir = (project_dir / "assets").resolve()
    image_path = (project_dir / relative_path).resolve()
    if not image_path.is_relative_to(assets_dir):
        raise ProjectImageError("Project images must be stored inside `assets/`.")
    if image_path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
        raise ProjectImageError(f"Unsupported image type: `{image_path.suffix or 'none'}`.")
    if not image_path.is_file():
        raise ProjectImageError(f"Image not found: `{relative_path}`.")
    return image_path


def find_markdown_tables(markdown: str) -> tuple[MarkdownTable, ...]:
    lines = markdown.splitlines(keepends=True)
    protected_lines: set[int] = set()
    for token in _MARKDOWN.parse(markdown):
        if token.type in {"fence", "code_block"} and token.map:
            protected_lines.update(range(token.map[0], token.map[1]))

    tables: list[MarkdownTable] = []
    line_index = 0
    while line_index < len(lines) - 1:
        if line_index in protected_lines or line_index + 1 in protected_lines:
            line_index += 1
            continue
        if not (_looks_like_table_row(lines[line_index]) and _looks_like_separator_row(lines[line_index + 1])):
            line_index += 1
            continue

        end_line = line_index + 2
        while (
            end_line < len(lines)
            and end_line not in protected_lines
            and _looks_like_table_row(lines[end_line])
        ):
            end_line += 1

        source = "".join(lines[line_index:end_line])
        tables.append(
            MarkdownTable(
                start_line=line_index,
                end_line=end_line,
                source=source,
                copy_text=format_markdown_table_for_plain_text(source),
                latex_text=format_markdown_table_for_latex(source),
            )
        )
        line_index = end_line
    return tuple(tables)


def format_markdown_table_for_plain_text(markdown_table: str) -> str:
    parsed = _parse_markdown_table(markdown_table)
    if parsed is None:
        return markdown_table.strip()
    headers, body, alignments = parsed
    return tabulate(
        body,
        headers=headers,
        tablefmt="github",
        colalign=alignments,
        disable_numparse=True,
    )


def format_markdown_table_for_latex(markdown_table: str) -> str:
    parsed = _parse_markdown_table(markdown_table)
    if parsed is None:
        return markdown_table.strip()
    headers, body, alignments = parsed
    return tabulate(
        body,
        headers=headers,
        tablefmt="latex_booktabs",
        colalign=alignments,
        disable_numparse=True,
    )


def _parse_markdown_table(markdown_table: str) -> tuple[list[str], list[list[str]], tuple[str, ...]] | None:
    rows = [_split_table_row(line) for line in markdown_table.splitlines() if line.strip()]
    if len(rows) < 2:
        return None

    column_count = max(len(row) for row in rows)
    normalized_rows = [row + [""] * (column_count - len(row)) for row in rows]
    headers = [cell.strip() for cell in normalized_rows[0]]
    body = [[cell.strip() for cell in row] for row in normalized_rows[2:]]
    alignments = tuple(_tabulate_alignment(cell) for cell in normalized_rows[1])
    return headers, body, alignments


def _looks_like_table_row(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and _has_unescaped_pipe(stripped) and len(_split_table_row(stripped)) >= 2


def _looks_like_separator_row(line: str) -> bool:
    cells = _split_table_row(line)
    if len(cells) < 2:
        return False
    return all(_is_separator_cell(cell) for cell in cells)


def _is_separator_cell(cell: str) -> bool:
    stripped = cell.strip()
    if stripped.startswith(":"):
        stripped = stripped[1:]
    if stripped.endswith(":"):
        stripped = stripped[:-1]
    return len(stripped) >= 3 and set(stripped) == {"-"}


def _tabulate_alignment(cell: str) -> str:
    stripped = cell.strip()
    if stripped.startswith(":") and stripped.endswith(":"):
        return "center"
    if stripped.endswith(":"):
        return "right"
    return "left"


def _has_unescaped_pipe(line: str) -> bool:
    escaped = False
    for char in line:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "|":
            return True
    return False


def _split_table_row(line: str) -> list[str]:
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for char in line.strip():
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\":
            current.append(char)
            escaped = True
            continue
        if char == "|":
            cells.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    cells.append("".join(current).strip())

    if cells and cells[0] == "":
        cells = cells[1:]
    if cells and cells[-1] == "":
        cells = cells[:-1]
    return cells
