from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt


SUPPORTED_IMAGE_SUFFIXES = {".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"}


class ProjectImageError(ValueError):
    pass


@dataclass(frozen=True)
class MarkdownImage:
    start_line: int
    end_line: int
    target: str
    alt: str


_MARKDOWN = MarkdownIt("commonmark")


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
