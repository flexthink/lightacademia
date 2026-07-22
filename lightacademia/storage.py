from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from .git_ops import git_commit_all, git_init, git_mv


RESERVED_PROJECT_NAMES = {"archived"}
HOME_NOTE = "Home.md"
PROJECT_SKILL = "SKILL.md"
PROJECT_DIRECTORIES = ("chats", "archived", "data", "assets", "code")
NOTEBOOK_READY_FILE = ".ready"
STOCK_NOTEBOOK_DIR = Path(__file__).resolve().parent.parent / "starter-notebook"


@dataclass(frozen=True)
class Project:
    name: str
    path: Path


@dataclass(frozen=True)
class Note:
    name: str
    path: Path


def slugify(value: str, fallback: str = "untitled") -> str:
    slug = safe_name(value, fallback)
    slug = slug.replace(" ", "-")
    slug = slug.strip(".-")
    return slug or fallback


def safe_name(value: str, fallback: str = "untitled") -> str:
    normalized = re.sub(r"\s+", " ", value)
    name = re.sub(r"[^A-Za-z0-9._ -]+", "", normalized).strip()
    name = name.strip(".- ")
    return name or fallback


def ensure_notebook(notebook_dir: Path) -> None:
    notebook_dir.mkdir(parents=True, exist_ok=True)
    (notebook_dir / "archived").mkdir(exist_ok=True)


def initialize_notebook(notebook_dir: Path, stock_notebook_dir: Path = STOCK_NOTEBOOK_DIR) -> bool:
    notebook_dir = notebook_dir.resolve()
    ready_file = notebook_dir / NOTEBOOK_READY_FILE
    if ready_file.is_file():
        return False

    ensure_notebook(notebook_dir)
    if not stock_notebook_dir.is_dir():
        raise FileNotFoundError(f"Stock notebook folder not found: {stock_notebook_dir}")

    for source in sorted(stock_notebook_dir.iterdir(), key=lambda path: path.name.lower()):
        if not source.is_dir() or source.name in RESERVED_PROJECT_NAMES or source.name.startswith("."):
            continue
        target = notebook_dir / source.name
        if target.exists():
            continue
        try:
            shutil.copytree(
                source,
                target,
                ignore=shutil.ignore_patterns(".git", ".ready", ".DS_Store", "__pycache__", "*.pyc"),
            )
            project = Project(target.name, target)
            ensure_project_directories(project)
            git_init(target)
            git_commit_all(target, "Create stock notebook")
        except Exception:
            if target.exists():
                shutil.rmtree(target)
            raise

    ready_file.write_text("Light Academia notebook initialized\n", encoding="utf-8")
    return True


def ensure_tools_dir(tools_dir: Path) -> None:
    tools_dir.mkdir(parents=True, exist_ok=True)


def ensure_project_directories(project: Project) -> None:
    for directory in PROJECT_DIRECTORIES:
        (project.path / directory).mkdir(exist_ok=True)


def list_projects(notebook_dir: Path) -> list[Project]:
    ensure_notebook(notebook_dir)
    projects = []
    for path in sorted(notebook_dir.iterdir(), key=lambda p: p.name.lower()):
        if not path.is_dir() or path.name in RESERVED_PROJECT_NAMES:
            continue
        projects.append(Project(path.name, path))
    return projects


def unique_child_path(parent: Path, stem: str, suffix: str = "") -> Path:
    candidate = parent / f"{stem}{suffix}"
    index = 2
    while candidate.exists():
        candidate = parent / f"{stem}-{index}{suffix}"
        index += 1
    return candidate


def home_note_boilerplate(title: str) -> str:
    return f"# {title}\n\n## Overview\n\n## Open Questions\n\n## Key Results\n"


def project_skill_boilerplate() -> str:
    return (
        "# Project Skill\n\n"
        "<!--\n"
        "Add terse project-specific guidance for the agent here.\n\n"
        "Use this file to describe:\n"
        "- Which researcher tools matter for this project\n"
        "- Relevant experiment naming conventions\n"
        "- Where fetched logs and metrics should go\n"
        "- How generated assets should be organized\n"
        "- Any project-specific safety constraints\n"
        "-->\n"
    )


def create_project(notebook_dir: Path, title: str) -> Project:
    ensure_notebook(notebook_dir)
    name = safe_name(title, "new project")
    if name in RESERVED_PROJECT_NAMES:
        name = "project"
    project_path = unique_child_path(notebook_dir, name)
    project_path.mkdir()
    ensure_project_directories(Project(project_path.name, project_path))

    created_at = datetime.now().astimezone().isoformat(timespec="seconds")
    meta = {
        "title": title.strip() or project_path.name,
        "created_at": created_at,
        "archived": False,
        "tags": [],
        "description": "",
    }
    (project_path / "meta.yaml").write_text(yaml.safe_dump(meta, sort_keys=False), encoding="utf-8")
    (project_path / HOME_NOTE).write_text(home_note_boilerplate(meta["title"]), encoding="utf-8")
    (project_path / PROJECT_SKILL).write_text(project_skill_boilerplate(), encoding="utf-8")

    git_init(project_path)
    git_commit_all(project_path, "Create project")
    return Project(project_path.name, project_path)


def archive_project(notebook_dir: Path, project: Project) -> Path:
    ensure_notebook(notebook_dir)
    git_commit_all(project.path, "Commit before archiving project")
    archive_root = notebook_dir / "archived"
    target = unique_child_path(archive_root, project.path.name)
    shutil.move(str(project.path), str(target))
    return target


def list_notes(project: Project) -> list[Note]:
    notes = []
    for path in sorted(project.path.glob("*.md"), key=lambda p: (p.name != HOME_NOTE, p.name.lower())):
        if path.is_file():
            notes.append(Note(path.name, path))
    return notes


def create_note(project: Project, title: str) -> Note:
    stem = safe_name(title, "new note")
    path = unique_child_path(project.path, stem, ".md")
    heading = title.strip() or path.stem.replace("-", " ").title()
    path.write_text(f"# {heading}\n\n", encoding="utf-8")
    return Note(path.name, path)


def read_note(note: Note) -> str:
    return note.path.read_text(encoding="utf-8")


def save_note(note: Note, content: str) -> None:
    note.path.write_text(content, encoding="utf-8")


def rename_note(project: Project, note: Note, new_title: str) -> Note:
    stem = safe_name(new_title, note.path.stem)
    target = unique_child_path(project.path, stem, ".md")
    git_mv(project.path, note.path, target)
    return Note(target.name, target)


def archive_note(project: Project, note: Note) -> Path:
    archived_dir = project.path / "archived"
    archived_dir.mkdir(exist_ok=True)
    target = unique_child_path(archived_dir, note.path.stem, note.path.suffix)
    git_mv(project.path, note.path, target)
    return target
