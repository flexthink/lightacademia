from __future__ import annotations

import argparse
import base64
import hashlib
import html
import importlib.metadata
import inspect
import io
import json
import logging
import mimetypes
import queue
import re
import shutil
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit_extras.resizable_columns as resizable_columns_module
from streamlit.components.v2.get_bidi_component_manager import get_bidi_component_manager
from streamlit.components.v2.manifest_scanner import ComponentConfig, ComponentManifest
from streamlit_extras.resizable_columns import resizable_columns

from lightacademia.actions import NoteAction, build_action_prompt, parse_note_actions
from lightacademia.agents import (
    AgentContext,
    AgentError,
    AgentProgress,
    AgentResult,
    AgentStopped,
    default_agent,
)
from lightacademia.chat import append_chat_entry, list_chat_log_dates, read_chat_log
from lightacademia.git_ops import (
    GitError,
    GitFileRevision,
    git_commit_all,
    git_file_at_revision,
    git_file_history,
    git_init,
    git_remote_url,
    git_set_remote_url,
    git_status_lines,
    git_sync,
    run_git,
)
from lightacademia.markdown_preview import (
    ProjectDataframeError,
    ProjectImageError,
    find_markdown_tables,
    find_standalone_dataframes,
    find_standalone_images,
    resolve_project_dataframe,
    resolve_project_image,
    rewrite_project_note_links,
)
from lightacademia.search import SearchUnavailable, search_notes
from lightacademia.storage import (
    HOME_NOTE,
    Project,
    archive_note,
    archive_project,
    create_note,
    create_project,
    ensure_project_directories,
    ensure_tools_dir,
    list_notes,
    list_projects,
    read_note,
    rename_note,
    save_note,
    slugify,
    unique_child_path,
)

logging.basicConfig(
    level=logging.INFO,
    force=True,  # Python 3.8+
)

logger = logging.getLogger(__name__)

try:
    from code_editor import code_editor
except Exception:  # pragma: no cover - optional Streamlit component
    code_editor = None


DEFAULT_NOTEBOOK_DIR = Path("notebook")
DEFAULT_TOOLS_DIR = Path("tools")
DEFAULT_AUTOCOMMIT_SECONDS = 5 * 60
PREVIEW_DEBOUNCE_SECONDS = 0.65
NOTE_PANE_HEIGHT = 430
ICON_BUTTON_LABEL = ""
LOGO_PATH = Path("assets/logo.png")
THEME_CSS_PATH = Path("assets/theme.css")
COPY_IMAGE_BUTTON_CSS_PATH = Path("assets/copy_image_button.css")
COPY_TABLE_BUTTONS_CSS_PATH = Path("assets/copy_table_buttons.css")
WORKSPACE_TREE_CSS_PATH = Path("assets/workspace_tree.css")
WORKSPACE_TREE_JS_PATH = Path("assets/workspace_tree.js")
PROJECT_QUERY_PARAM = "project"
NOTE_QUERY_PARAM = "note"
RESIZABLE_COLUMNS_COMPONENT = "streamlit-extras.resizable_columns"
MARKDOWN_SUFFIXES = {".md", ".markdown"}
TEXT_SUFFIXES = {
    ".bash",
    ".cfg",
    ".conf",
    ".css",
    ".csv",
    ".env",
    ".ini",
    ".js",
    ".json",
    ".jsonl",
    ".log",
    ".md",
    ".markdown",
    ".py",
    ".r",
    ".rs",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".txt",
    ".yaml",
    ".yml",
    ".zsh",
}
COPY_IMAGE_BUTTON_HTML = '<button type="button" class="la-copy-image-button">📋 Copy image</button>'
COPY_IMAGE_BUTTON_JS = """
export default function(component) {
  const { data, parentElement } = component;
  const button = parentElement.querySelector("button");
  if (!button || !data) {
    return;
  }

  const originalLabel = data.label || "📋 Copy image";
  button.textContent = originalLabel;
  button.onclick = async () => {
    try {
      const response = await fetch(data.dataUri);
      const blob = await response.blob();
      await navigator.clipboard.write([
        new ClipboardItem({ [data.mimeType]: blob })
      ]);
      button.textContent = data.copiedLabel || "Copied";
      setTimeout(() => { button.textContent = originalLabel; }, 1400);
    } catch (error) {
      console.error(error);
      button.textContent = data.errorLabel || "Copy failed";
      setTimeout(() => { button.textContent = originalLabel; }, 1800);
    }
  };
}
"""
COPY_TABLE_BUTTONS_HTML = """
<div class="la-copy-table-buttons">
  <button type="button" class="la-copy-table-button" data-copy-kind="markdown" title="Copy Markdown table">📋</button>
  <button type="button" class="la-copy-table-button" data-copy-kind="latex" title="Copy LaTeX table">∑</button>
</div>
"""
COPY_TABLE_BUTTONS_JS = """
export default function(component) {
  const { data, parentElement } = component;
  if (!data) {
    return;
  }

  const buttons = parentElement.querySelectorAll("button[data-copy-kind]");
  buttons.forEach((button) => {
    const originalLabel = button.textContent;
    const copyKind = button.getAttribute("data-copy-kind");
    button.onclick = async () => {
      try {
        const text = copyKind === "latex" ? data.latexText : data.markdownText;
        await navigator.clipboard.writeText(text);
        button.textContent = "✓";
        setTimeout(() => { button.textContent = originalLabel; }, 1000);
      } catch (error) {
        console.error(error);
        button.textContent = "!";
        setTimeout(() => { button.textContent = originalLabel; }, 1400);
      }
    };
  });
}
"""
WORKSPACE_TREE_HTML = '<div class="la-workspace-tree" role="tree"></div>'
EDITOR_COMPONENT_CSS = """
:root {
  --streamlit-light-background-color: #fffaf1;
  --streamlit-light-select-color: #eee7d7;
  --streamlit-light-select-highlight-color: #e4eadf;
  --streamlit-light-primary-text-color: #2e2923;
  --streamlit-light-secondary-text-color: #6f675b;
  --streamlit-light-info-color: #8f3f35;
  --streamlit-light-editor-border-radius: 7px;
}
body,
#root,
#root ~ div,
.streamlit_code-editor,
.ace_editor,
.ace_scroller,
.ace_content {
  background: #fffaf1 !important;
}
.ace-streamlit-light,
.ace-streamlit-light .ace_gutter {
  background-color: #fffaf1 !important;
  color: #6f675b !important;
}
.ace-streamlit-light .ace_marker-layer .ace_active-line,
.ace-streamlit-light .ace_gutter-active-line {
  background: #eee7d7 !important;
}
.ace-streamlit-light .ace_marker-layer .ace_selection {
  background: #e4eadf !important;
}
.ace-streamlit-light .ace_cursor {
  color: #8f3f35 !important;
}
"""


def register_resizable_columns_component() -> None:
    manager = get_bidi_component_manager()
    if manager.get_component_path(RESIZABLE_COLUMNS_COMPONENT):
        return

    package_root = Path(inspect.getfile(resizable_columns_module)).resolve().parents[1]
    manifest = ComponentManifest(
        name="streamlit-extras",
        version=importlib.metadata.version("streamlit-extras"),
        components=[
            ComponentConfig(
                name="resizable_columns",
                asset_dir="resizable_columns/frontend/build",
            )
        ],
    )
    manager.register_from_manifest(manifest, package_root)


def apply_theme() -> None:
    if THEME_CSS_PATH.exists():
        css = THEME_CSS_PATH.read_text(encoding="utf-8")
        st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


@lru_cache(maxsize=1)
def copy_image_button_component():
    css = COPY_IMAGE_BUTTON_CSS_PATH.read_text(encoding="utf-8") if COPY_IMAGE_BUTTON_CSS_PATH.exists() else ""
    return st.components.v2.component(
        "copy_image_button",
        html=COPY_IMAGE_BUTTON_HTML,
        css=css,
        js=COPY_IMAGE_BUTTON_JS,
    )


@lru_cache(maxsize=1)
def copy_table_buttons_component():
    css = COPY_TABLE_BUTTONS_CSS_PATH.read_text(encoding="utf-8") if COPY_TABLE_BUTTONS_CSS_PATH.exists() else ""
    return st.components.v2.component(
        "copy_table_buttons",
        html=COPY_TABLE_BUTTONS_HTML,
        css=css,
        js=COPY_TABLE_BUTTONS_JS,
    )


@lru_cache(maxsize=1)
def workspace_tree_component():
    css = WORKSPACE_TREE_CSS_PATH.read_text(encoding="utf-8") if WORKSPACE_TREE_CSS_PATH.exists() else ""
    js = WORKSPACE_TREE_JS_PATH.read_text(encoding="utf-8") if WORKSPACE_TREE_JS_PATH.exists() else ""
    return st.components.v2.component(
        "light_academia_workspace_tree",
        html=WORKSPACE_TREE_HTML,
        css=css,
        js=js,
    )


def workspace_tree_selection(
    tree: dict[str, object],
    selected: str | None,
    key: str,
    tree_id: str,
) -> str | None:
    result = workspace_tree_component()(
        key=key,
        data={"tree": tree, "selected": selected or "", "expandedDepth": 2, "treeId": tree_id},
        default={"selected": selected or ""},
        on_selected_change=lambda: None,
    )
    if not isinstance(result, dict):
        return selected
    value = result.get("selected")
    return value if isinstance(value, str) and value else selected


@lru_cache(maxsize=1)
def logo_data_uri() -> str | None:
    if not LOGO_PATH.exists():
        return None
    encoded = base64.b64encode(LOGO_PATH.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def note_display_name(note_name: str) -> str:
    return Path(note_name).stem if note_name.endswith(".md") else note_name


def history_entry_label(revision: GitFileRevision) -> str:
    when = revision.committed_at.strftime("%Y-%m-%d %H:%M")
    short_commit = revision.commit[:7]
    return f"{when} · {short_commit} · {revision.subject}"


def render_app_header(note_name: str | None = None) -> None:
    logo_uri = logo_data_uri()
    logo_html = (
        f'<img class="la-logo" src="{logo_uri}" alt="Light Academia logo">'
        if logo_uri
        else '<span class="la-logo-fallback">✨🎻📚</span>'
    )
    note_html = (
        f'<span class="la-header-separator">|</span><span class="la-current-note">{html.escape(note_display_name(note_name))}</span>'
        if note_name
        else ""
    )
    st.markdown(
        f"""
        <header class="la-app-header">
            {logo_html}
            <div class="la-wordmark" aria-label="Light Academia">
                <span>Light</span>
                <span>Academia</span>
            </div>
            {note_html}
        </header>
        """,
        unsafe_allow_html=True,
    )


def render_note_header_title(note_name: str) -> None:
    st.markdown(
        f"""
        <div class="la-title-inline">
            <span class="la-header-separator">|</span>
            <span class="la-current-note">{html.escape(note_display_name(note_name))}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


@dataclass(frozen=True)
class AppConfig:
    notebook_dir: Path
    tools_dir: Path
    autocommit_seconds: int
    agent: str
    agent_timeout_seconds: int


@dataclass
class AgentRunState:
    run_id: str
    project: Project
    note_name: str
    prompt: str
    tools_dir: Path
    agent: str
    agent_timeout_seconds: int
    before_status: set[str]
    progress: queue.Queue[AgentProgress]
    stop_requested: threading.Event
    thread: threading.Thread | None = None
    result: AgentResult | None = None
    response_message: str | None = None
    error: BaseException | None = None
    done: bool = False


@dataclass(frozen=True)
class ToolDocument:
    name: str
    path: Path


@st.cache_resource
def agent_runs() -> dict[str, AgentRunState]:
    return {}


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--notebook", type=Path, default=DEFAULT_NOTEBOOK_DIR)
    parser.add_argument("--tools", type=Path, default=DEFAULT_TOOLS_DIR)
    parser.add_argument("--autocommit-seconds", type=int, default=DEFAULT_AUTOCOMMIT_SECONDS)
    parser.add_argument("--agent", choices=("codex", "claude"), default="codex")
    parser.add_argument("--agent-timeout-seconds", type=int, default=3600)
    args, _ = parser.parse_known_args()
    return AppConfig(
        notebook_dir=args.notebook.expanduser(),
        tools_dir=args.tools.expanduser(),
        autocommit_seconds=max(30, args.autocommit_seconds),
        agent=args.agent,
        agent_timeout_seconds=max(60, args.agent_timeout_seconds),
    )


@st.dialog("New project", icon=":material/create_new_folder:")
def new_project_dialog(notebook_dir: Path) -> None:
    title = st.text_input("Project name", key="new_project_title")
    if st.button("Create", type="primary", icon=":material/add:"):
        if not title.strip():
            st.warning("Enter a project name.")
            return
        try:
            project = create_project(notebook_dir, title)
            st.session_state.project_name = project.name
            st.session_state.note_name = HOME_NOTE
            set_selection_query(project.name, HOME_NOTE)
            st.session_state.editor_revision += 1
            st.rerun()
        except (OSError, GitError) as exc:
            st.error(f"Could not create project: {exc}")


@st.dialog("Archive project", icon=":material/archive:")
def archive_project_dialog(notebook_dir: Path, project: Project, current_note_to_save=None) -> None:
    st.write(f"Archive `{project.name}`?")
    if st.button("Archive", type="primary", icon=":material/archive:"):
        try:
            if current_note_to_save is not None:
                save_editor_state(current_note_to_save)
            archive_project(notebook_dir, project)
            st.session_state.project_name = None
            st.session_state.note_name = None
            set_selection_query(None)
            st.session_state.editor_revision += 1
            st.rerun()
        except (OSError, GitError) as exc:
            st.error(f"Could not archive project: {exc}")


@st.dialog("Settings", icon=":material/settings:")
def settings_dialog(project: Project, note) -> None:
    note_title = st.text_input("Name", value=note.path.stem, key=f"settings_note_name_{project.name}_{note.name}")
    current_remote = git_remote_url(project.path) or ""
    remote_url = st.text_input("Remote URL", value=current_remote, key=f"remote_url_{project.name}")
    st.caption("Leave empty and save to remove the configured remote.")
    if st.button("Save", type="primary", icon=":material/save:"):
        if not note_title.strip():
            st.warning("Enter a note name.")
            return
        try:
            if note_title.strip() != note.path.stem:
                save_editor_state(note)
                renamed = rename_note(project, note, note_title)
                st.session_state.note_name = renamed.name
                set_selection_query(project.name, renamed.name)
                st.session_state.editor_revision += 1
            git_set_remote_url(project.path, remote_url)
            st.session_state.last_sync_error = None
            st.session_state.last_sync_message = "Settings saved."
            st.rerun()
        except (OSError, GitError) as exc:
            st.error(f"Could not save settings: {exc}")


@st.dialog("New note", icon=":material/note_add:")
def new_note_dialog(project: Project) -> None:
    title = st.text_input("Note title", key="new_note_title")
    if st.button("Create", type="primary", icon=":material/add:"):
        if not title.strip():
            st.warning("Enter a note title.")
            return
        note = create_note(project, title)
        st.session_state.note_name = note.name
        set_selection_query(project.name, note.name)
        st.session_state.editor_revision += 1
        st.rerun()


@st.dialog("Archive note", icon=":material/archive:")
def archive_note_dialog(project: Project, note) -> None:
    st.write(f"Archive `{note.name}`?")
    if st.button("Archive", type="primary", icon=":material/archive:"):
        try:
            save_editor_state(note)
            archive_note(project, note)
            st.session_state.note_name = HOME_NOTE
            set_selection_query(project.name, HOME_NOTE)
            st.session_state.editor_revision += 1
            st.rerun()
        except (OSError, GitError) as exc:
            st.error(f"Could not archive note: {exc}")


@st.dialog("Add image", icon=":material/add_photo_alternate:")
def add_image_dialog(project: Project, note) -> None:
    uploaded = st.file_uploader(
        "Image",
        type=["png", "jpg", "jpeg", "gif", "webp", "svg"],
        key=f"upload_image_{project.name}_{note.name}",
    )
    alt_text = st.text_input("Alt text", key=f"upload_image_alt_{project.name}_{note.name}")
    if st.button("Add image", type="primary", icon=":material/add_photo_alternate:"):
        if uploaded is None:
            st.warning("Choose an image.")
            return
        try:
            save_editor_state(note)
            asset_path = save_uploaded_project_image(project, uploaded.name, uploaded.getvalue())
            relative_path = asset_path.relative_to(project.path).as_posix()
            alt = alt_text.strip() or asset_path.stem.replace("-", " ").replace("_", " ")
            image_markdown = f"![{alt}]({relative_path})"
            insert_markdown_at_editor_cursor(note, image_markdown)
            st.session_state.source_visible = True
            st.rerun()
        except OSError as exc:
            st.error(f"Could not add image: {exc}")


@st.dialog("Add link", icon=":material/link:")
def add_link_dialog(project: Project, note) -> None:
    notes = [candidate for candidate in list_notes(project) if candidate.name != note.name]
    if not notes:
        st.info("No other notes to link to.")
        return

    options = [candidate.name for candidate in notes]
    target_note_name = st.selectbox(
        "Page",
        options=options,
        format_func=note_display_name,
        key=f"link_target_{project.name}_{note.name}",
    )
    if st.button("Add link", type="primary", icon=":material/link:"):
        try:
            save_editor_state(note)
            link_markdown = f"[{note_display_name(target_note_name)}]({target_note_name})"
            insert_markdown_at_editor_cursor(note, link_markdown)
            st.session_state.source_visible = True
            st.rerun()
        except OSError as exc:
            st.error(f"Could not add link: {exc}")


@st.dialog("Note history", icon=":material/history:")
def note_history_dialog(project: Project, note) -> None:
    save_editor_state(note)
    try:
        revisions = git_file_history(project.path, note.path)
    except GitError as exc:
        st.error(f"Could not read note history: {exc}")
        return

    if not revisions:
        st.info("No history for this note yet.")
        return

    for revision in revisions:
        label_col, button_col = st.columns([0.78, 0.22], vertical_alignment="center")
        with label_col:
            st.markdown(history_entry_label(revision))
        with button_col:
            if st.button(
                "View",
                key=f"view_history_{note.name}_{revision.commit}",
                icon=":material/visibility:",
            ):
                st.session_state.history_revision = revision.commit
                st.session_state.history_note_name = note.name
                st.session_state.history_label = history_entry_label(revision)
                st.session_state.source_visible = False
                st.rerun()


@st.dialog("Tool history", icon=":material/history:")
def tool_history_dialog(tools_dir: Path, path: Path) -> None:
    save_tool_editor_state(path)
    checkpoint_tools(tools_dir, "Checkpoint before viewing tool history")
    try:
        revisions = git_file_history(tools_dir.resolve(), path.resolve())
    except GitError as exc:
        st.error(f"Could not read tool history: {exc}")
        return

    if not revisions:
        st.info("No history for this tool file yet.")
        return

    relative_path = path.resolve().relative_to(tools_dir.resolve()).as_posix()
    for revision in revisions:
        label_col, button_col = st.columns([0.78, 0.22], vertical_alignment="center")
        with label_col:
            st.markdown(history_entry_label(revision))
        with button_col:
            if st.button(
                "View",
                key=f"view_tool_history_{relative_path}_{revision.commit}",
                icon=":material/visibility:",
            ):
                st.session_state.tool_history_revision = revision.commit
                st.session_state.tool_history_path = relative_path
                st.session_state.tool_history_label = history_entry_label(revision)
                st.rerun()


@st.dialog("Import tools", icon=":material/upload_file:")
def import_tools_dialog(tools_dir: Path) -> None:
    uploaded = st.file_uploader(
        "Tools zip",
        type=["zip"],
        key=f"import_tools_zip_{tools_dir.resolve()}",
    )
    st.caption("Files from the zip are written into tools/. Existing paths are overwritten. Git history is kept.")
    if st.button("Import", type="primary", icon=":material/upload_file:"):
        if uploaded is None:
            st.warning("Choose a zip file.")
            return
        try:
            save_selected_tool_editor_state(tools_dir)
            checkpoint_tools(tools_dir, "Checkpoint before importing tools")
            imported = import_tools_zip(tools_dir, uploaded.getvalue())
            checkpoint_tools(tools_dir, "Import tools")
            clear_tool_history_revision()
            st.session_state.tool_editor_revision += 1
            st.session_state.workspace_tree_revision += 1
            st.session_state.tools_import_message = f"Imported {imported} files."
            st.rerun(scope="app")
        except zipfile.BadZipFile:
            st.error("The uploaded file is not a valid zip archive.")
        except (OSError, GitError) as exc:
            st.error(f"Could not import tools: {exc}")


@st.dialog("New tools folder", icon=":material/create_new_folder:")
def new_tool_folder_dialog(tools_dir: Path) -> None:
    parent_options = tool_parent_options(tools_dir)
    parent = st.selectbox(
        "Location",
        parent_options,
        index=tool_parent_default_index(tools_dir, parent_options),
        format_func=tool_parent_label,
        key="new_tool_folder_parent",
    )
    name = st.text_input("Folder name", key="new_tool_folder_name")
    if st.button("Create", type="primary", icon=":material/create_new_folder:"):
        error = validate_tool_entry_name(name)
        if error:
            st.warning(error)
            return
        target = tools_dir.resolve() / parent / name.strip()
        if target.exists():
            st.warning("A file or folder with that name already exists.")
            return
        try:
            save_selected_tool_editor_state(tools_dir)
            checkpoint_tools(tools_dir, "Checkpoint before creating tool folder")
            target.mkdir()
            marker = target / ".gitkeep"
            marker.write_text("", encoding="utf-8")
            run_git(
                tools_dir.resolve(),
                "add",
                "--",
                marker.relative_to(tools_dir.resolve()).as_posix(),
            )
            checkpoint_tools(tools_dir, f"Create tools folder {target.relative_to(tools_dir.resolve()).as_posix()}")
            relative_target = target.relative_to(tools_dir.resolve()).as_posix()
            st.session_state.selected_tool_path = relative_target
            refresh_tools_view(f"Created folder {relative_target}.")
        except (OSError, GitError) as exc:
            st.error(f"Could not create folder: {exc}")


@st.dialog("New tool file", icon=":material/note_add:")
def new_tool_file_dialog(tools_dir: Path) -> None:
    parent_options = tool_parent_options(tools_dir)
    parent = st.selectbox(
        "Location",
        parent_options,
        index=tool_parent_default_index(tools_dir, parent_options),
        format_func=tool_parent_label,
        key="new_tool_file_parent",
    )
    name = st.text_input("File name", placeholder="example.py", key="new_tool_file_name")
    st.caption("Only text and code files can be created. Syntax highlighting follows the file extension.")
    if st.button("Create", type="primary", icon=":material/note_add:"):
        error = validate_tool_entry_name(name)
        if error:
            st.warning(error)
            return
        cleaned_name = name.strip()
        if not is_supported_tool_filename(cleaned_name):
            st.warning("Use a recognized text or code file extension.")
            return
        target = tools_dir.resolve() / parent / cleaned_name
        if target.exists():
            st.warning("A file or folder with that name already exists.")
            return
        try:
            save_selected_tool_editor_state(tools_dir)
            checkpoint_tools(tools_dir, "Checkpoint before creating tool file")
            target.write_text("", encoding="utf-8")
            marker = target.parent / ".gitkeep"
            if marker.is_file():
                marker.unlink()
            checkpoint_tools(tools_dir, f"Create tool {target.relative_to(tools_dir.resolve()).as_posix()}")
            st.session_state.selected_tool_path = target.relative_to(tools_dir.resolve()).as_posix()
            refresh_tools_view(f"Created {st.session_state.selected_tool_path}.")
        except (OSError, GitError) as exc:
            st.error(f"Could not create file: {exc}")


@st.dialog("Delete tool", icon=":material/delete:")
def delete_tool_file_dialog(tools_dir: Path, path: Path) -> None:
    relative_path = path.resolve().relative_to(tools_dir.resolve()).as_posix()
    item_type = "folder and all of its contents" if path.is_dir() else "file"
    st.warning(f"Delete the {item_type} `{relative_path}`? This cannot be undone from the current view.")
    if st.button("Delete", type="primary", icon=":material/delete:"):
        try:
            if path.is_file() and is_editable_tool_file(path):
                save_tool_editor_state(path)
            checkpoint_tools(tools_dir, "Checkpoint before deleting tool file")
            git_rm_args = ["rm"]
            if path.is_dir():
                git_rm_args.append("-r")
            git_rm_args.extend(["--ignore-unmatch", "--", relative_path])
            run_git(tools_dir.resolve(), *git_rm_args)
            if path.is_dir() and path.exists():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
            if path.parent != tools_dir.resolve() and not any(path.parent.iterdir()):
                marker = path.parent / ".gitkeep"
                marker.write_text("", encoding="utf-8")
                run_git(
                    tools_dir.resolve(),
                    "add",
                    "--",
                    marker.relative_to(tools_dir.resolve()).as_posix(),
                )
            if not checkpoint_tools(tools_dir, f"Delete tool {relative_path}"):
                run_git(tools_dir.resolve(), "commit", "--allow-empty", "-m", f"Delete tool {relative_path}")
            st.session_state.selected_tool_path = None
            clear_tool_history_revision()
            refresh_tools_view(f"Deleted {relative_path}.")
        except (OSError, GitError) as exc:
            st.error(f"Could not delete file: {exc}")


@st.dialog("Agent chat history", icon=":material/forum:")
def chat_history_dialog(project: Project) -> None:
    available_dates = list_chat_log_dates(project.path)
    default_date = available_dates[0] if available_dates else date.today()
    selected_date = st.date_input(
        "Date",
        value=default_date,
        key=f"chat_history_date_{project.name}",
    )

    if isinstance(selected_date, tuple):
        selected_date = selected_date[0] if selected_date else default_date

    content = read_chat_log(project.path, selected_date)
    if not content.strip():
        st.info(f"No agent chats recorded for {selected_date:%Y-%m-%d}.")
        return

    with st.container(height=560, border=True):
        st.markdown(content)


def init_state() -> None:
    defaults = {
        "project_name": None,
        "note_name": None,
        "last_saved_note_key": None,
        "last_edit_at": None,
        "last_commit_at": None,
        "editor_revision": 0,
        "preview_note_key": None,
        "preview_content": "",
        "preview_pending_content": "",
        "preview_pending_at": None,
        "preview_source_key": None,
        "preview_source_content": "",
        "preview_source_project_dir": None,
        "last_agent_response": None,
        "last_agent_error": None,
        "agent_running": False,
        "active_agent_run_id": None,
        "agent_progress_entries": [],
        "agent_progress_chars": 0,
        "requested_action": None,
        "source_visible": False,
        "editor_cursors": {},
        "history_revision": None,
        "history_note_name": None,
        "history_label": None,
        "last_sync_error": None,
        "last_sync_message": None,
        "workspace_view": "notes",
        "selected_tool_path": None,
        "tool_editor_revision": 0,
        "workspace_tree_revision": 0,
        "tools_import_message": None,
        "tools_action_message": None,
        "tool_last_edit_at": None,
        "tool_last_commit_at": None,
        "tool_history_revision": None,
        "tool_history_path": None,
        "tool_history_label": None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def query_param_value(key: str) -> str | None:
    value = st.query_params.get(key)
    if isinstance(value, list):
        return value[0] if value else None
    return value if isinstance(value, str) and value else None


def hydrate_selection_from_query(projects: list[Project]) -> None:
    requested_project = query_param_value(PROJECT_QUERY_PARAM)
    if not requested_project:
        return

    projects_by_name = {project.name: project for project in projects}
    project = projects_by_name.get(requested_project)
    if project is None:
        return

    st.session_state.project_name = project.name
    requested_note = query_param_value(NOTE_QUERY_PARAM)
    if not requested_note:
        return

    note_names = {note.name for note in list_notes(project)}
    if requested_note in note_names:
        st.session_state.note_name = requested_note


def set_selection_query(project_name: str | None, note_name: str | None = None) -> None:
    if not project_name:
        st.query_params.clear()
        return
    updates = {PROJECT_QUERY_PARAM: project_name}
    if note_name:
        updates[NOTE_QUERY_PARAM] = note_name
    if dict(st.query_params) != updates:
        st.query_params.clear()
        st.query_params.update(updates)


def sync_selection_to_query(project: Project, note_name: str | None) -> None:
    set_selection_query(project.name, note_name)


def active_history_revision(note) -> str | None:
    revision = st.session_state.get("history_revision")
    note_name = st.session_state.get("history_note_name")
    if isinstance(revision, str) and note_name == note.name:
        return revision
    return None


def clear_history_revision() -> None:
    st.session_state.history_revision = None
    st.session_state.history_note_name = None
    st.session_state.history_label = None


def active_tool_history_revision(path: Path, tools_dir: Path) -> str | None:
    revision = st.session_state.get("tool_history_revision")
    history_path = st.session_state.get("tool_history_path")
    try:
        relative_path = path.resolve().relative_to(tools_dir.resolve()).as_posix()
    except ValueError:
        return None
    if isinstance(revision, str) and history_path == relative_path:
        return revision
    return None


def clear_tool_history_revision() -> None:
    st.session_state.tool_history_revision = None
    st.session_state.tool_history_path = None
    st.session_state.tool_history_label = None


def current_project(projects: list[Project]) -> Project | None:
    if not projects:
        return None
    names = [project.name for project in projects]
    if st.session_state.project_name not in names:
        st.session_state.project_name = names[0]
    return projects[names.index(st.session_state.project_name)]


def current_note(notes):
    if not notes:
        return None
    names = [note.name for note in notes]
    if st.session_state.note_name not in names:
        st.session_state.note_name = HOME_NOTE if HOME_NOTE in names else names[0]
    return notes[names.index(st.session_state.note_name)]


def commit_if_idle(project: Project, autocommit_seconds: int) -> None:
    last_edit_at = st.session_state.get("last_edit_at")
    last_commit_at = st.session_state.get("last_commit_at")
    if not last_edit_at:
        return
    if last_commit_at and last_commit_at >= last_edit_at:
        return
    if time.time() - last_edit_at < autocommit_seconds:
        return
    if git_commit_all(project.path, "Autosave checkpoint"):
        st.session_state.last_commit_at = time.time()
        st.toast("Autosave checkpoint committed")


def is_tools_git_candidate(path: Path, tools_dir: Path) -> bool:
    if not path.is_file():
        return False
    try:
        relative_parts = path.relative_to(tools_dir).parts
    except ValueError:
        return False
    if ".git" in relative_parts:
        return False
    if not is_editable_tool_file(path):
        return False
    try:
        path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return True


def git_stage_tools_text_files(tools_dir: Path) -> None:
    root = tools_dir.resolve()
    run_git(root, "add", "-u")
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix().lower()):
        if is_tools_git_candidate(path, root):
            run_git(root, "add", "--", path.relative_to(root).as_posix())


def git_has_staged_changes(repo_dir: Path) -> bool:
    result = run_git(repo_dir, "diff", "--cached", "--quiet", check=False)
    return result.returncode == 1


def git_commit_tools_text_files(tools_dir: Path, message: str) -> bool:
    if not (tools_dir / ".git").exists():
        return False
    git_stage_tools_text_files(tools_dir)
    if not git_has_staged_changes(tools_dir):
        return False
    run_git(tools_dir, "commit", "-m", message)
    return True


def ensure_tools_git_excludes(tools_dir: Path) -> None:
    exclude_path = tools_dir / ".git" / "info" / "exclude"
    if not exclude_path.exists():
        return
    existing = exclude_path.read_text(encoding="utf-8")
    patterns = ["__pycache__/", "*.py[cod]", ".DS_Store"]
    missing = [pattern for pattern in patterns if pattern not in existing.splitlines()]
    if missing:
        suffix = "" if existing.endswith("\n") or not existing else "\n"
        exclude_path.write_text(f"{existing}{suffix}" + "\n".join(missing) + "\n", encoding="utf-8")


def ensure_tools_git_repo(tools_dir: Path) -> None:
    tools_dir = tools_dir.resolve()
    if (tools_dir / ".git").exists():
        ensure_tools_git_excludes(tools_dir)
        return
    git_init(tools_dir)
    ensure_tools_git_excludes(tools_dir)
    git_commit_tools_text_files(tools_dir, "Create tools repo")


def commit_tools_if_idle(tools_dir: Path, autocommit_seconds: int) -> None:
    last_edit_at = st.session_state.get("tool_last_edit_at")
    last_commit_at = st.session_state.get("tool_last_commit_at")
    if not last_edit_at:
        return
    if last_commit_at and last_commit_at >= last_edit_at:
        return
    if time.time() - last_edit_at < autocommit_seconds:
        return
    if git_commit_tools_text_files(tools_dir, "Autosave tools checkpoint"):
        st.session_state.tool_last_commit_at = time.time()
        st.toast("Tools checkpoint committed")


def create_tools_zip(tools_dir: Path) -> bytes:
    root = tools_dir.resolve()
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix().lower()):
            relative_path = path.relative_to(root)
            if ".git" in relative_path.parts:
                continue
            if path.is_file():
                zip_file.write(path, relative_path.as_posix())
    return archive.getvalue()


def safe_tools_zip_members(zip_file: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = []
    for member in zip_file.infolist():
        relative_path = Path(member.filename)
        if member.filename.endswith("/") or member.is_dir():
            continue
        if (
            relative_path.is_absolute()
            or ".." in relative_path.parts
            or ".git" in relative_path.parts
            or (relative_path.parts and re.match(r"^[A-Za-z]:$", relative_path.parts[0]))
        ):
            continue
        members.append(member)
    return members


def import_tools_zip(tools_dir: Path, content: bytes) -> int:
    root = tools_dir.resolve()
    imported = 0
    with zipfile.ZipFile(io.BytesIO(content)) as zip_file:
        for member in safe_tools_zip_members(zip_file):
            target = (root / member.filename).resolve()
            if not target.is_relative_to(root):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zip_file.open(member) as source, target.open("wb") as destination:
                destination.write(source.read())
            imported += 1
    return imported


def editor_key(note) -> str:
    return f"editor_{note.name}_{st.session_state.editor_revision}"


def note_pane_height() -> int:
    return NOTE_PANE_HEIGHT


def save_editor_state(note) -> bool:
    if st.session_state.get("agent_running"):
        return False
    if active_history_revision(note) is not None:
        return False
    value = st.session_state.get(editor_key(note))
    if isinstance(value, dict):
        value = value.get("text")
    if not isinstance(value, str):
        return False
    if value == read_note(note):
        return False
    save_note(note, value)
    st.session_state.last_edit_at = time.time()
    return True


def save_uploaded_project_image(project: Project, filename: str, content: bytes) -> Path:
    assets_dir = project.path / "assets"
    assets_dir.mkdir(exist_ok=True)
    source_name = Path(filename or "image").name
    suffix = Path(source_name).suffix.lower()
    if suffix not in {".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"}:
        suffix = ".png"
    stem = slugify(Path(source_name).stem, "image")
    target = unique_child_path(assets_dir, stem, suffix)
    target.write_bytes(content)
    return target


def current_editor_content(note) -> str:
    value = st.session_state.get(editor_key(note))
    if isinstance(value, dict):
        value = value.get("text")
    return value if isinstance(value, str) else read_note(note)


def insert_markdown_at_editor_cursor(note, snippet: str) -> None:
    content = current_editor_content(note)
    cursor = st.session_state.get("editor_cursors", {}).get(note.name)
    offset = cursor_to_offset(content, cursor)
    insertion = format_snippet_insertion(content, snippet, offset)
    updated = f"{content[:offset]}{insertion}{content[offset:]}"
    save_note(note, updated)
    st.session_state.last_edit_at = time.time()
    st.session_state.editor_revision += 1


def format_snippet_insertion(content: str, snippet: str, offset: int) -> str:
    prefix = "" if offset == 0 or content[offset - 1] == "\n" else "\n\n"
    suffix = "" if offset >= len(content) or content[offset : offset + 1] == "\n" else "\n\n"
    return f"{prefix}{snippet}{suffix}"


def cursor_to_offset(content: str, cursor) -> int:
    parsed = parse_editor_cursor(cursor)
    if isinstance(parsed, int):
        return max(0, min(len(content), parsed))
    if isinstance(parsed, dict):
        row = parsed.get("row", parsed.get("line"))
        column = parsed.get("column", parsed.get("col"))
        if isinstance(row, int) and isinstance(column, int):
            return row_column_to_offset(content, row, column)
    return len(content)


def parse_editor_cursor(cursor):
    if isinstance(cursor, str):
        stripped = cursor.strip()
        if not stripped:
            return None
        if stripped.isdigit():
            return int(stripped)
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            match = re.search(r"row['\"]?\s*:\s*(\d+).*column['\"]?\s*:\s*(\d+)", stripped)
            if match:
                return {"row": int(match.group(1)), "column": int(match.group(2))}
            return None
    if isinstance(cursor, dict) and isinstance(cursor.get("start"), dict):
        return cursor["start"]
    return cursor


def row_column_to_offset(content: str, row: int, column: int) -> int:
    if row <= 0:
        return max(0, min(len(content), column))
    lines = content.splitlines(keepends=True)
    if row >= len(lines):
        return len(content)
    return sum(len(line) for line in lines[:row]) + max(0, min(len(lines[row]), column))


def remember_editor_cursor(note, response) -> None:
    if not isinstance(response, dict):
        return
    cursor = response.get("cursor")
    if cursor in (None, ""):
        return
    cursors = st.session_state.setdefault("editor_cursors", {})
    cursors[note.name] = cursor


def editor_response_content(response, fallback: str) -> str:
    if not isinstance(response, dict):
        return fallback
    response_text = response.get("text")
    response_type = response.get("type")
    if response_type == "" and response_text == "":
        return fallback
    return response_text if isinstance(response_text, str) else fallback


def is_markdown_file(path: Path) -> bool:
    return path.suffix.lower() in MARKDOWN_SUFFIXES


def is_editable_tool_file(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.suffix.lower() in TEXT_SUFFIXES:
        return True
    mime_type = mimetypes.guess_type(path.name)[0] or ""
    return mime_type.startswith("text/")


def is_visible_tool_path(path: Path) -> bool:
    return not any(part.startswith(".") for part in path.parts)


def is_supported_tool_filename(name: str) -> bool:
    path = Path(name)
    if not path.suffix:
        return False
    if path.suffix.lower() in TEXT_SUFFIXES:
        return True
    mime_type = mimetypes.guess_type(path.name)[0] or ""
    return mime_type.startswith("text/")


def tool_parent_options(tools_dir: Path) -> list[str]:
    root = tools_dir.resolve()
    directories = ["."]
    directories.extend(
        path.relative_to(root).as_posix()
        for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix().lower())
        if path.is_dir() and is_visible_tool_path(path.relative_to(root))
    )
    return directories


def tool_parent_label(relative_path: str) -> str:
    return "Tools root" if relative_path == "." else relative_path


def tool_parent_default_index(tools_dir: Path, options: list[str]) -> int:
    selected_path = resolve_tool_tree_selection(tools_dir, st.session_state.get("selected_tool_path"))
    if selected_path is None:
        return 0
    parent_path = selected_path if selected_path.is_dir() else selected_path.parent
    parent = parent_path.relative_to(tools_dir.resolve()).as_posix()
    parent = "." if parent == "." else parent
    return options.index(parent) if parent in options else 0


def validate_tool_entry_name(name: str) -> str | None:
    cleaned = name.strip()
    if not cleaned:
        return "Enter a name."
    if cleaned in {".", ".."} or "/" in cleaned or "\\" in cleaned:
        return "Enter a single file or folder name, without a path."
    if cleaned.startswith("."):
        return "Hidden file and folder names are not supported here."
    return None


def refresh_tools_view(message: str) -> None:
    clear_tool_history_revision()
    st.session_state.tool_editor_revision += 1
    st.session_state.workspace_tree_revision += 1
    st.session_state.tools_action_message = message
    st.rerun(scope="app")


def tool_file_language(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".bash": "sh",
        ".c": "c",
        ".cc": "cpp",
        ".cpp": "cpp",
        ".cs": "csharp",
        ".css": "css",
        ".csv": "text",
        ".go": "golang",
        ".java": "java",
        ".js": "javascript",
        ".json": "json",
        ".jsonl": "json",
        ".md": "markdown",
        ".markdown": "markdown",
        ".py": "python",
        ".r": "r",
        ".rb": "ruby",
        ".rs": "rust",
        ".sh": "sh",
        ".sql": "sql",
        ".toml": "toml",
        ".ts": "typescript",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".zsh": "sh",
    }.get(suffix, "text")


def build_tools_tree(tools_dir: Path) -> dict[str, object]:
    root = tools_dir.resolve()
    tree: dict[str, object] = {}
    if not root.exists():
        return tree
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix().lower()):
        relative_path = path.relative_to(root)
        if not is_visible_tool_path(relative_path):
            continue
        relative_parts = relative_path.parts
        cursor = tree
        for part in relative_parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[relative_parts[-1]] = None if path.is_file() else cursor.get(relative_parts[-1], {})
    return tree


def resolve_tool_tree_selection(tools_dir: Path, selected: str | None) -> Path | None:
    if not selected:
        return None
    root = tools_dir.resolve()
    candidate = (root / selected).resolve()
    if not candidate.is_relative_to(root) or not candidate.exists() or candidate == root:
        return None
    return candidate


def resolve_tool_selection(tools_dir: Path, selected: str | None) -> Path | None:
    candidate = resolve_tool_tree_selection(tools_dir, selected)
    if candidate is None or not candidate.is_file():
        return None
    return candidate


def tool_editor_key(path: Path) -> str:
    return f"tool_editor_{path.resolve()}_{st.session_state.tool_editor_revision}"


def read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def save_tool_file(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    st.session_state.tool_last_edit_at = time.time()


def save_tool_editor_state(path: Path) -> bool:
    if st.session_state.get("tool_history_revision") is not None:
        return False
    key = tool_editor_key(path)
    value = st.session_state.get(key)
    if isinstance(value, dict):
        value = value.get("text")
    if not isinstance(value, str):
        return False
    if value == read_text_file(path):
        return False
    save_tool_file(path, value)
    return True


def save_selected_tool_editor_state(tools_dir: Path) -> None:
    selected_tool = resolve_tool_selection(tools_dir, st.session_state.get("selected_tool_path"))
    if selected_tool is not None and is_editable_tool_file(selected_tool):
        save_tool_editor_state(selected_tool)


def checkpoint_tools(tools_dir: Path, message: str = "Tools checkpoint") -> bool:
    if git_commit_tools_text_files(tools_dir, message):
        st.session_state.tool_last_commit_at = time.time()
        return True
    return False


def render_tool_code_editor(path: Path) -> str:
    content = read_text_file(path)
    key = tool_editor_key(path)
    if code_editor is not None:
        response = code_editor(
            content,
            lang=tool_file_language(path),
            theme="streamlit_light",
            height=[34, 34],
            key=key,
            response_mode=["debounce", "blur"],
            options={"wrap": True, "fontSize": 14},
            component_props={"css": EDITOR_COMPONENT_CSS},
        )
        return editor_response_content(response, content)
    return st.text_area("File", value=content, height=note_pane_height(), key=key, label_visibility="collapsed")


def render_tool_download_card(path: Path, tools_dir: Path) -> None:
    relative_path = path.relative_to(tools_dir.resolve()).as_posix()
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    try:
        size_bytes = path.stat().st_size
        file_bytes = path.read_bytes()
    except OSError as exc:
        st.error(f"Could not read tool file: {exc}")
        return

    with st.container(border=True):
        st.markdown(f"**{html.escape(path.name)}**")
        st.caption(f"{relative_path} · {mime_type} · {size_bytes:,} bytes")
        st.download_button(
            "Download",
            data=file_bytes,
            file_name=path.name,
            mime=mime_type,
            key=f"download_selected_tool_{relative_path}",
            icon=":material/download:",
        )


def commit_before_navigation(
    project: Project,
    current_note_to_save=None,
    next_project: str | None = None,
    next_note: str | None = None,
) -> None:
    if current_note_to_save is not None:
        save_editor_state(current_note_to_save)
    if git_commit_all(project.path, "Checkpoint before navigation"):
        st.session_state.last_commit_at = time.time()
    if next_project is not None:
        st.session_state.project_name = next_project
    if next_note is not None:
        st.session_state.note_name = next_note
    clear_history_revision()
    set_selection_query(
        next_project or project.name,
        next_note if next_note is not None else (None if next_project is not None else st.session_state.note_name),
    )
    st.session_state.editor_revision += 1
    st.rerun()


def render_editor(note) -> str:
    content = read_note(note)
    key = editor_key(note)
    if code_editor is not None:
        response = code_editor(
            content,
            lang="markdown",
            theme="streamlit_light",
            height=[34, 34],
            key=key,
            response_mode=["debounce", "blur"],
            options={"wrap": True, "fontSize": 14},
            component_props={"css": EDITOR_COMPONENT_CSS},
        )
        remember_editor_cursor(note, response)
        return editor_response_content(response, content)
    return st.text_area("Markdown", value=content, height=note_pane_height(), key=key, label_visibility="collapsed")


def render_tools_sidebar(tools_dir: Path) -> None:
    import_message = st.session_state.pop("tools_import_message", None)
    if import_message:
        st.success(import_message)
    action_message = st.session_state.pop("tools_action_message", None)
    if action_message:
        st.success(action_message)

    if st.button("Notes", key="show_notes_view", icon=":material/sticky_note_2:", width="stretch"):
        save_selected_tool_editor_state(tools_dir)
        checkpoint_tools(tools_dir, "Checkpoint before leaving tools")
        st.session_state.workspace_view = "notes"
        st.rerun()

    selected_path = resolve_tool_tree_selection(tools_dir, st.session_state.get("selected_tool_path"))
    create_delete_cols = st.columns(3)
    with create_delete_cols[0]:
        if st.button(
            ICON_BUTTON_LABEL,
            key="open_new_tool_folder",
            help="New folder",
            icon=":material/create_new_folder:",
            width="stretch",
        ):
            new_tool_folder_dialog(tools_dir)
    with create_delete_cols[1]:
        if st.button(
            ICON_BUTTON_LABEL,
            key="open_new_tool_file",
            help="New file",
            icon=":material/note_add:",
            width="stretch",
        ):
            new_tool_file_dialog(tools_dir)
    with create_delete_cols[2]:
        if st.button(
            ICON_BUTTON_LABEL,
            key="open_delete_tool_file",
            help="Delete file or folder",
            icon=":material/delete:",
            width="stretch",
            disabled=selected_path is None,
        ) and selected_path is not None:
            delete_tool_file_dialog(tools_dir, selected_path)

    st.markdown("### Tools")
    tree = build_tools_tree(tools_dir)
    if tree:
        current_selected = st.session_state.get("selected_tool_path")
        selected = workspace_tree_selection(
            tree,
            current_selected,
            key=f"workspace_tree_{tools_dir.resolve()}_{st.session_state.workspace_tree_revision}",
            tree_id=f"tools:{tools_dir.resolve()}",
        )
        if selected:
            selected_path = resolve_tool_tree_selection(tools_dir, selected)
            if selected_path is not None and selected != current_selected:
                save_selected_tool_editor_state(tools_dir)
                checkpoint_tools(tools_dir, "Checkpoint before switching tools")
                st.session_state.selected_tool_path = selected
                clear_tool_history_revision()
                st.session_state.tool_editor_revision += 1
                st.rerun()
    else:
        st.caption("No tool files.")

    st.divider()
    st.markdown("#### Import / Export")
    action_cols = st.columns(2)
    with action_cols[0]:
        if st.button(
            "Import",
            key="open_import_tools",
            help="Import tools",
            icon=":material/upload_file:",
            width="stretch",
        ):
            import_tools_dialog(tools_dir)
    with action_cols[1]:
        save_selected_tool_editor_state(tools_dir)
        st.download_button(
            "Export",
            data=create_tools_zip(tools_dir),
            file_name="tools.zip",
            mime="application/zip",
            key="export_tools",
            help="Export tools",
            icon=":material/download:",
            width="stretch",
        )


def render_tools_workspace(tools_dir: Path) -> None:
    selected_path = resolve_tool_tree_selection(tools_dir, st.session_state.get("selected_tool_path"))
    if selected_path is None:
        st.session_state.selected_tool_path = None
        render_app_header("Tools")
        st.info("Select a tool file or folder from the sidebar.")
        return

    relative_path = selected_path.relative_to(tools_dir.resolve()).as_posix()
    if selected_path.is_dir():
        render_app_header(relative_path)
        st.info("Folder selected. Create a file inside it or use the sidebar delete button to remove it recursively.")
        return

    history_revision = active_tool_history_revision(selected_path, tools_dir)
    is_history_view = history_revision is not None
    brand_col, title_col, actions_col = st.columns([0.28, 0.52, 0.20], vertical_alignment="center")
    with brand_col:
        render_app_header()
    with title_col:
        st.markdown(
            f'<div class="la-current-note">{html.escape(relative_path)}</div>',
            unsafe_allow_html=True,
        )
    with actions_col:
        with st.container(
            key="tool_header_actions",
            horizontal=True,
            vertical_alignment="center",
            gap="small",
        ):
            if is_history_view:
                if st.button("Current", key="return_current_tool", icon=":material/history_toggle_off:"):
                    clear_tool_history_revision()
                    st.rerun()
            if st.button(
                ICON_BUTTON_LABEL,
                key="open_tool_history",
                help="History",
                icon=":material/history:",
                disabled=not is_editable_tool_file(selected_path),
            ):
                tool_history_dialog(tools_dir, selected_path)

    if is_history_view:
        try:
            historical_content = git_file_at_revision(tools_dir.resolve(), selected_path.resolve(), history_revision)
        except GitError as exc:
            st.error(f"Could not read historical tool revision: {exc}")
            clear_tool_history_revision()
            st.rerun()
        st.markdown(
            f'<div class="la-history-badge">Historical view · {html.escape(str(st.session_state.get("tool_history_label") or history_revision[:7]))}</div>',
            unsafe_allow_html=True,
        )
        if is_markdown_file(selected_path):
            pseudo_project = Project("tools", tools_dir.resolve())
            pseudo_note = ToolDocument(selected_path.name, selected_path)
            render_preview(pseudo_project, pseudo_note, historical_content, allow_actions=False, source_suffix=f":tools:{history_revision}")
        else:
            st.code(historical_content, language=tool_file_language(selected_path))
        return

    try:
        if is_markdown_file(selected_path):
            editor_col, preview_col = resizable_columns([0.52, 0.48], min_width=320, key="tools_markdown_columns")
            with editor_col:
                edited_content = render_tool_code_editor(selected_path)
            if edited_content != read_text_file(selected_path):
                save_tool_file(selected_path, edited_content)
            with preview_col:
                pseudo_project = Project("tools", tools_dir.resolve())
                pseudo_note = ToolDocument(selected_path.name, selected_path)
                render_preview(pseudo_project, pseudo_note, edited_content, allow_actions=False, source_suffix=":tools")
        elif is_editable_tool_file(selected_path):
            edited_content = render_tool_code_editor(selected_path)
            if edited_content != read_text_file(selected_path):
                save_tool_file(selected_path, edited_content)
        else:
            render_tool_download_card(selected_path, tools_dir)
    except UnicodeDecodeError:
        st.warning("This file is not valid UTF-8 text and cannot be edited here.")
    except OSError as exc:
        st.error(f"Could not edit tool file: {exc}")


def preview_key(note) -> str:
    return f"{note.path}:{st.session_state.editor_revision}"


def debounced_preview_content(key: str, content: str) -> str:
    now = time.time()
    if st.session_state.preview_note_key != key:
        st.session_state.preview_note_key = key
        st.session_state.preview_content = content
        st.session_state.preview_pending_content = content
        st.session_state.preview_pending_at = now
        return content

    if content != st.session_state.preview_pending_content:
        st.session_state.preview_pending_content = content
        st.session_state.preview_pending_at = now

    pending_at = st.session_state.preview_pending_at
    if pending_at is None or now - pending_at >= PREVIEW_DEBOUNCE_SECONDS:
        st.session_state.preview_content = st.session_state.preview_pending_content
    elif st.session_state.preview_content == "":
        st.session_state.preview_content = st.session_state.preview_pending_content

    return st.session_state.preview_content


@st.fragment(run_every="700ms")
def render_preview_fragment() -> None:
    source_key = st.session_state.get("preview_source_key")
    source_content = st.session_state.get("preview_source_content", "")
    source_project_dir = st.session_state.get("preview_source_project_dir")
    allow_actions = bool(st.session_state.get("preview_allow_actions", True))
    if not source_key or not source_project_dir:
        return
    preview_content = debounced_preview_content(source_key, source_content)
    with st.container(key="preview_pane"):
        render_project_markdown(preview_content, Path(source_project_dir), source_key, allow_actions=allow_actions)


def render_project_markdown(markdown: str, project_dir: Path, source_key: str, allow_actions: bool = True) -> None:
    rendered_markdown, note_link_errors = rewrite_project_note_links(markdown, project_dir)
    for error in note_link_errors:
        st.warning(error)

    lines = rendered_markdown.splitlines(keepends=True)
    cursor = 0
    rendered = False
    events = []
    if allow_actions:
        events.extend(
            (action.line - 1, action.end_line, "action", action)
            for action in parse_note_actions(markdown).actions
        )
    events.extend(
        (image.start_line, image.end_line, "image", image)
        for image in find_standalone_images(markdown)
    )
    events.extend(
        (dataframe.start_line, dataframe.end_line, "dataframe", dataframe)
        for dataframe in find_standalone_dataframes(markdown)
    )
    events.extend(
        (table.start_line, table.end_line, "table", table)
        for table in find_markdown_tables(markdown)
    )
    events.sort(key=lambda event: (event[0], event[2]))

    for start_line, end_line, event_type, event in events:
        preceding_markdown = "".join(lines[cursor:start_line])
        if preceding_markdown.strip():
            st.markdown(preceding_markdown)
            rendered = True

        if event_type == "action":
            action_key = hashlib.sha256(
                f"{source_key}:{event.line}:{event.name}".encode("utf-8")
            ).hexdigest()[:16]
            if st.button(
                f"**Run:** {event.name}",
                key=f"preview_action_{action_key}",
                icon=":material/bolt:",
                help="Select this action",
            ):
                st.session_state.requested_action = event
                st.rerun()
            with st.expander("Action description", expanded=False):
                action_markdown = "".join(lines[start_line:end_line])
                if action_markdown.strip():
                    st.markdown(action_markdown)
        elif event_type == "image":
            try:
                image_path = resolve_project_image(project_dir, event.target)
            except ProjectImageError as exc:
                st.warning(str(exc))
            else:
                render_copyable_image(image_path, event.alt, source_key, start_line)
                st.image(image_path, caption=event.alt or None, width="stretch")
        elif event_type == "dataframe":
            try:
                dataframe_path = resolve_project_dataframe(project_dir, event.target)
            except ProjectDataframeError as exc:
                st.warning(str(exc))
            else:
                render_project_dataframe(
                    dataframe_path,
                    project_dir,
                    columns=event.columns,
                    annotation_error=event.annotation_error,
                )
        else:
            table_markdown = "".join(lines[start_line:end_line])
            render_copyable_table(table_markdown, event.copy_text, event.latex_text, source_key, start_line)
        rendered = True
        cursor = end_line

    remaining_markdown = "".join(lines[cursor:])
    if remaining_markdown.strip():
        st.markdown(remaining_markdown)
        rendered = True
    if not rendered:
        st.markdown(" ")


def render_copyable_table(table_markdown: str, copy_text: str, latex_text: str, source_key: str, start_line: int) -> None:
    copy_key = hashlib.sha256(f"{source_key}:table:{start_line}:{copy_text}".encode("utf-8")).hexdigest()[:16]
    copy_table_buttons_component()(
        data={"markdownText": copy_text, "latexText": latex_text},
        key=f"copy_table_buttons_{copy_key}",
        height=42,
    )
    st.markdown(table_markdown)


def render_project_dataframe(
    dataframe_path: Path,
    project_dir: Path,
    columns: dict[str, str] | None = None,
    annotation_error: str | None = None,
) -> None:
    project_root = project_dir.resolve()
    relative_path = dataframe_path.resolve().relative_to(project_root)
    try:
        dataframe = pd.read_csv(dataframe_path)
    except Exception as exc:
        st.warning(f"Could not read dataframe `{relative_path}`: {exc}")
        return
    if columns:
        dataframe = dataframe.rename(columns=columns)
    if annotation_error:
        st.warning(annotation_error)
    st.caption(str(relative_path))
    st.dataframe(dataframe, width="stretch", height=360)


def render_copyable_image(image_path: Path, alt: str, source_key: str, start_line: int) -> None:
    mime_type = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"
    if not mime_type.startswith("image/"):
        return

    copy_key = hashlib.sha256(f"{source_key}:image:{start_line}:{image_path}".encode("utf-8")).hexdigest()[:16]
    data_uri = f"data:{mime_type};base64,{base64.b64encode(image_path.read_bytes()).decode('ascii')}"
    copy_image_button_component()(
        data={
            "dataUri": data_uri,
            "mimeType": mime_type,
            "label": "📋 Copy image",
            "copiedLabel": "Copied",
            "errorLabel": "Copy failed",
        },
        key=f"copy_image_{copy_key}",
        height=44,
    )


def render_preview(project: Project, note, content: str, allow_actions: bool = True, source_suffix: str = "") -> None:
    st.session_state.preview_source_key = f"{preview_key(note)}{source_suffix}"
    st.session_state.preview_source_content = content
    st.session_state.preview_source_project_dir = str(project.path)
    st.session_state.preview_allow_actions = allow_actions
    render_preview_fragment()


def reload_note_from_disk() -> None:
    st.session_state.editor_revision += 1
    st.session_state.requested_action = None
    clear_history_revision()
    st.session_state.preview_note_key = None
    st.session_state.preview_content = ""
    st.session_state.preview_pending_content = ""
    st.session_state.preview_pending_at = None
    st.session_state.preview_source_key = None
    st.session_state.preview_source_content = ""
    st.session_state.preview_source_project_dir = None


def action_option_label(action: NoteAction | None) -> str:
    return "Choose an action..." if action is None else action.name


def action_selector_key(note) -> str:
    return f"selected_action_{note.name}_{st.session_state.editor_revision}"


def apply_requested_action(note, actions: tuple[NoteAction, ...]) -> None:
    requested_action = st.session_state.get("requested_action")
    if requested_action is None:
        return
    selected_action = next((action for action in actions if action == requested_action), None)
    if selected_action is not None:
        st.session_state[action_selector_key(note)] = selected_action
    st.session_state.requested_action = None


def sync_project(project: Project, note) -> None:
    save_editor_state(note)
    with st.spinner("Syncing notebook..."):
        try:
            result = git_sync(project.path)
        except GitError as exc:
            st.session_state.last_sync_error = f"Sync failed: {exc}"
            st.session_state.last_sync_message = None
            st.rerun()

    st.session_state.last_sync_error = None
    st.session_state.last_sync_message = result.message
    reload_note_from_disk()
    st.rerun()


def start_agent_command(
    project: Project,
    note,
    prompt: str,
    tools_dir: Path,
    agent: str,
    agent_timeout_seconds: int,
) -> AgentRunState:
    save_editor_state(note)
    git_commit_all(project.path, "Checkpoint before agent command")
    st.session_state.last_commit_at = time.time()

    run_id = uuid.uuid4().hex
    run_state = AgentRunState(
        run_id=run_id,
        project=project,
        note_name=note.name if note is not None else "",
        prompt=prompt,
        tools_dir=tools_dir,
        agent=agent,
        agent_timeout_seconds=agent_timeout_seconds,
        before_status=set(git_status_lines(project.path)),
        progress=queue.Queue(),
        stop_requested=threading.Event(),
    )
    run_state.thread = threading.Thread(
        target=_agent_worker,
        args=(run_state,),
        name=f"lightacademia-agent-{run_id[:8]}",
        daemon=True,
    )
    agent_runs()[run_id] = run_state
    st.session_state.active_agent_run_id = run_id
    st.session_state.agent_running = True
    st.session_state.agent_progress_entries = []
    st.session_state.agent_progress_chars = 0
    logger.info("Starting agent run %s for project=%s note=%s", run_id, project.name, run_state.note_name)
    run_state.thread.start()
    return run_state


def _agent_worker(run_state: AgentRunState) -> None:
    agent = default_agent(run_state.agent, timeout_seconds=run_state.agent_timeout_seconds)
    context = AgentContext(
        project_dir=run_state.project.path,
        project_name=run_state.project.name,
        tools_dir=run_state.tools_dir,
        current_note=run_state.note_name or None,
    )
    try:
        result = agent.run(
            run_state.prompt,
            context,
            on_progress=lambda progress: record_agent_progress(run_state, progress),
            should_stop=run_state.stop_requested.is_set,
        )
        after_status = git_status_lines(run_state.project.path)
        changed_lines = [line for line in after_status if line not in run_state.before_status] or after_status
        log_path = append_chat_entry(
            run_state.project.path,
            run_state.prompt,
            result.response,
            agent_name=agent.name,
            tool_actions=result.tool_actions,
            file_changes=changed_lines,
        )
        git_commit_all(run_state.project.path, "[agent] Run agent command")
        run_state.result = result
        run_state.response_message = f"{result.response}\n\nLogged to `{log_path.relative_to(run_state.project.path)}`."
        logger.info("Agent run %s finished successfully", run_state.run_id)
    except Exception as exc:
        run_state.error = exc
        logger.info("Agent run %s failed: %s", run_state.run_id, exc)
    finally:
        run_state.done = True


def record_agent_progress(run_state: AgentRunState, progress: AgentProgress) -> None:
    entry = f"[{progress.event_type}]\n{progress.text}"
    logger.info("Agent progress:\n%s", entry)
    run_state.progress.put(progress)


def active_agent_run() -> AgentRunState | None:
    run_id = st.session_state.get("active_agent_run_id")
    if not isinstance(run_id, str):
        return None
    run_state = agent_runs().get(run_id)
    if run_state is None:
        st.session_state.agent_running = False
        st.session_state.active_agent_run_id = None
    return run_state


def drain_agent_progress(run_state: AgentRunState) -> None:
    entries = st.session_state.setdefault("agent_progress_entries", [])
    progress_chars = int(st.session_state.get("agent_progress_chars") or 0)
    while True:
        try:
            progress = run_state.progress.get_nowait()
        except queue.Empty:
            break
        entry = f"[{progress.event_type}]\n{progress.text}"
        entries.append(entry)
        progress_chars += len(entry)
        while progress_chars > 100_000 and len(entries) > 1:
            progress_chars -= len(entries.pop(0))
    st.session_state.agent_progress_chars = progress_chars


def finish_agent_run(run_state: AgentRunState) -> None:
    drain_agent_progress(run_state)
    st.session_state.agent_running = False
    st.session_state.active_agent_run_id = None
    agent_runs().pop(run_state.run_id, None)

    if run_state.error is None:
        st.session_state.last_agent_response = run_state.response_message
        st.session_state.last_agent_error = None
        st.session_state.last_commit_at = time.time()
    elif isinstance(run_state.error, AgentStopped):
        st.session_state.last_agent_error = "Agent stopped."
    else:
        st.session_state.last_agent_error = f"Could not run agent: {run_state.error}"
    reload_note_from_disk()


def render_agent_panel(project: Project, note, actions: tuple[NoteAction, ...], tools_dir: Path) -> None:
    run_state = active_agent_run()
    with st.container(key="agent_panel"):
        with st.expander("Agent chat", expanded=True):
            if run_state is not None:
                render_agent_progress_panel()
            else:
                render_agent_form(project, note, actions, tools_dir)


@st.fragment(run_every="250ms")
def render_agent_progress_panel() -> None:
    run_state = active_agent_run()
    if run_state is None:
        return
    drain_agent_progress(run_state)
    entries = st.session_state.get("agent_progress_entries", [])
    status_label = "Stopping 🤖 Robot..." if run_state.stop_requested.is_set() else "🤖 Robot is working..."
    with st.status(status_label, expanded=True, state="running"):
        stop_col, _ = st.columns([0.2, 0.8])
        with stop_col:
            if st.button(
                "Stop",
                key=f"stop_agent_{run_state.run_id}",
                icon=":material/stop_circle:",
                disabled=run_state.stop_requested.is_set(),
            ):
                run_state.stop_requested.set()
                st.rerun()
        st.container(height=220, border=False).code(
            "\n\n".join(entries) if entries else "Waiting for 🤖 Robot output...",
            language=None,
        )
    if run_state.done:
        finish_agent_run(run_state)
        st.rerun(scope="app")


def render_agent_form(project: Project, note, actions: tuple[NoteAction, ...], tools_dir: Path) -> None:
    action_input, prompt_input = st.columns([0.34, 0.66], vertical_alignment="top")
    with action_input:
        selected_action = st.selectbox(
            "Action",
            [None, *actions],
            format_func=action_option_label,
            key=action_selector_key(note),
            label_visibility="collapsed",
        )
    with prompt_input:
        prompt_key = f"agent_prompt_{note.name}_{st.session_state.editor_revision}"
        prompt = st.text_area("Message", height=120, key=prompt_key, label_visibility="collapsed")
    submitted = st.button(
        "Run agent",
        key=f"run_agent_{note.name}_{st.session_state.editor_revision}",
    )
    if submitted and (prompt.strip() or selected_action is not None):
        agent_prompt = (
            build_action_prompt(selected_action, note.name, prompt)
            if selected_action is not None
            else prompt.strip()
        )
        try:
            config = get_config()
            start_agent_command(
                project,
                note,
                agent_prompt,
                tools_dir,
                config.agent,
                config.agent_timeout_seconds,
            )
            st.session_state.last_agent_error = None
        except (OSError, GitError, AgentError) as exc:
            st.session_state.last_agent_error = f"Could not start agent: {exc}"
        st.rerun()


def main() -> None:
    st.set_page_config(page_title="Light Academia", page_icon="📚", layout="wide")
    register_resizable_columns_component()
    init_state()
    config = get_config()
    apply_theme()

    notebook_dir = config.notebook_dir
    tools_dir = config.tools_dir

    try:
        ensure_tools_dir(tools_dir)
        ensure_tools_git_repo(tools_dir)
        projects = list_projects(notebook_dir)
    except (OSError, GitError) as exc:
        st.error(f"Could not open workspace folders: {exc}")
        return
    hydrate_selection_from_query(projects)

    if st.session_state.get("workspace_view") == "tools":
        try:
            commit_tools_if_idle(tools_dir, config.autocommit_seconds)
        except GitError as exc:
            st.warning(f"Tools autosave commit failed: {exc}")
        with st.sidebar:
            render_tools_sidebar(tools_dir)
        render_tools_workspace(tools_dir)
        return

    project = current_project(projects)
    if project is None:
        with st.sidebar:
            if st.button("Tools", key="show_tools_view_empty", icon=":material/construction:", width="stretch"):
                st.session_state.workspace_view = "tools"
                st.rerun()
            if st.button(
                ICON_BUTTON_LABEL,
                key="open_new_project_empty",
                help="New project",
                icon=":material/create_new_folder:",
            ):
                new_project_dialog(notebook_dir)
        render_app_header()
        st.info("Create a project to get started.")
        return
    try:
        ensure_project_directories(project)
    except OSError as exc:
        st.error(f"Could not prepare project folders: {exc}")
        return

    try:
        commit_if_idle(project, config.autocommit_seconds)
        commit_tools_if_idle(tools_dir, config.autocommit_seconds)
    except GitError as exc:
        st.warning(f"Autosave commit failed: {exc}")

    note_before_project_navigation = current_note(list_notes(project))

    with st.sidebar:
        if st.button("Tools", key="show_tools_view", icon=":material/construction:", width="stretch"):
            if note_before_project_navigation is not None:
                save_editor_state(note_before_project_navigation)
            st.session_state.workspace_view = "tools"
            st.rerun()
        st.divider()

        project_names = [item.name for item in projects]
        project_header, project_new, project_archive = st.columns([0.66, 0.17, 0.17], vertical_alignment="bottom")
        with project_header:
            selected_project = st.selectbox(
                "Projects",
                project_names,
                index=project_names.index(project.name),
            )
        with project_new:
            if st.button(
                ICON_BUTTON_LABEL,
                key="open_new_project",
                help="New project",
                icon=":material/create_new_folder:",
            ):
                new_project_dialog(notebook_dir)
        with project_archive:
            if st.button(
                ICON_BUTTON_LABEL,
                key="open_archive_project",
                help="Archive project",
                icon=":material/archive:",
            ):
                archive_project_dialog(notebook_dir, project, note_before_project_navigation)
        if selected_project != project.name:
            commit_before_navigation(
                project,
                current_note_to_save=note_before_project_navigation,
                next_project=selected_project,
                next_note=None,
            )

        st.divider()
        if st.button(
            ICON_BUTTON_LABEL,
            key="open_new_note",
            help="New note",
            icon=":material/note_add:",
        ):
            new_note_dialog(project)

    notes = list_notes(project)
    note = current_note(notes)
    if note is None:
        render_app_header()
        st.info("Create a note to get started.")
        return
    sync_selection_to_query(project, note.name)

    with st.sidebar:
        note_names = [item.name for item in notes]
        selected_note = st.radio(
            "Notes",
            note_names,
            index=note_names.index(note.name),
            format_func=note_display_name,
        )
        if selected_note != note.name:
            commit_before_navigation(project, current_note_to_save=note, next_note=selected_note)

        st.divider()
        search_key = f"note_search_{project.name}"
        search_query = st.text_input(
            "Search",
            key=search_key,
            placeholder="Search notes...",
        )
        if search_query.strip():
            try:
                search_results = search_notes(notes, search_query, limit=6)
            except SearchUnavailable as exc:
                st.warning(str(exc))
                search_results = []
            except OSError as exc:
                st.warning(f"Could not search notes: {exc}")
                search_results = []

            if not search_results:
                st.caption("No matches.")
            for index, result in enumerate(search_results):
                if st.button(
                    note_display_name(result.note.name),
                    key=f"search_result_{project.name}_{result.note.name}_{index}",
                    help=result.snippet,
                    icon=":material/search:",
                    width="stretch",
                ):
                    commit_before_navigation(project, current_note_to_save=note, next_note=result.note.name)
                st.caption(result.snippet)
            if st.button(
                "Clear search",
                key=f"clear_search_{project.name}",
                icon=":material/close:",
                width="stretch",
            ):
                st.session_state[search_key] = ""
                st.rerun()

    history_revision = active_history_revision(note)
    is_history_view = history_revision is not None

    remote_configured = git_remote_url(project.path) is not None

    brand_col, title_group_col, spacer_col, actions_col = st.columns(
        [0.28, 0.34, 0.04, 0.34],
        vertical_alignment="center",
    )
    with brand_col:
        render_app_header()
    with title_group_col:
        with st.container(
            key="note_title_action",
            horizontal=True,
            vertical_alignment="center",
            gap="small",
        ):
            render_note_header_title(note.name)
    with spacer_col:
        st.write("")
    with actions_col:
        with st.container(
            key="header_actions",
            horizontal=True,
            vertical_alignment="center",
            gap="small",
        ):
            if is_history_view:
                if st.button("Current", key="return_current_note", icon=":material/history_toggle_off:"):
                    clear_history_revision()
                    st.session_state.source_visible = True
                    st.rerun()
            else:
                source_visible = bool(st.session_state.get("source_visible", False))
                next_source_visible = not source_visible
                toggle_help = "View only" if source_visible else "Edit"
                toggle_icon = ":material/visibility:" if source_visible else ":material/edit:"
                if st.button(
                    ICON_BUTTON_LABEL,
                    key="toggle_source_visible",
                    help=toggle_help,
                    icon=toggle_icon,
                ):
                    if source_visible:
                        save_editor_state(note)
                    st.session_state.source_visible = next_source_visible
                    st.rerun()
            if st.button(
                ICON_BUTTON_LABEL,
                key="open_note_history",
                help="History",
                icon=":material/history:",
            ):
                note_history_dialog(project, note)
            if st.button(
                ICON_BUTTON_LABEL,
                key="open_chat_history",
                help="Agent chat history",
                icon=":material/forum:",
            ):
                chat_history_dialog(project)
            if st.button(
                ICON_BUTTON_LABEL,
                key="open_settings",
                help="Settings",
                icon=":material/settings:",
            ):
                settings_dialog(project, note)
            if st.button(
                ICON_BUTTON_LABEL,
                key="open_add_image",
                help="Add image",
                icon=":material/add_photo_alternate:",
                disabled=is_history_view,
            ):
                add_image_dialog(project, note)
            if st.button(
                ICON_BUTTON_LABEL,
                key="open_add_link",
                help="Add link",
                icon=":material/link:",
                disabled=is_history_view,
            ):
                add_link_dialog(project, note)
            if st.button(
                ICON_BUTTON_LABEL,
                key="sync_project",
                help="Sync",
                icon=":material/sync:",
                disabled=not remote_configured or is_history_view,
            ):
                sync_project(project, note)
            if st.button(
                ICON_BUTTON_LABEL,
                key="open_archive_note",
                help="Archive note",
                icon=":material/archive:",
                disabled=note.name == HOME_NOTE or is_history_view,
            ):
                archive_note_dialog(project, note)

    if is_history_view:
        try:
            edited_content = git_file_at_revision(project.path, note.path, history_revision)
        except GitError as exc:
            st.error(f"Could not read historical note revision: {exc}")
            clear_history_revision()
            st.rerun()
        st.markdown(
            f'<div class="la-history-badge">Historical view · {html.escape(str(st.session_state.get("history_label") or history_revision[:7]))}</div>',
            unsafe_allow_html=True,
        )
        render_preview(project, note, edited_content, allow_actions=False, source_suffix=f":{history_revision}")
    elif st.session_state.get("source_visible", False):
        editor_col, preview_col = resizable_columns([0.52, 0.48], min_width=320, key="editor_preview_columns")
        with editor_col:
            edited_content = render_editor(note)
        original_content = read_note(note)
        if not st.session_state.get("agent_running") and edited_content != original_content:
            save_note(note, edited_content)
            st.session_state.last_edit_at = time.time()
        with preview_col:
            render_preview(project, note, edited_content)
    else:
        edited_content = read_note(note)
        render_preview(project, note, edited_content)

    action_source = read_note(note) if is_history_view else edited_content
    action_result = parse_note_actions(action_source)
    if not is_history_view:
        apply_requested_action(note, action_result.actions)
    for error in action_result.errors:
        st.warning(f"Action block at line {error.line}: {error.message}")

    if st.session_state.last_sync_error:
        st.warning(st.session_state.last_sync_error)
    elif st.session_state.last_sync_message:
        st.info(st.session_state.last_sync_message)

    if st.session_state.last_agent_error:
        st.error(st.session_state.last_agent_error)
    if st.session_state.last_agent_response:
        summary_col, dismiss_col = st.columns([0.94, 0.06], vertical_alignment="top")
        with summary_col:
            st.markdown(st.session_state.last_agent_response)
        with dismiss_col:
            if st.button(
                ICON_BUTTON_LABEL,
                key="dismiss_agent_summary",
                help="Dismiss agent summary",
                icon=":material/close:",
            ):
                st.session_state.last_agent_response = None
                st.rerun()

    if not is_history_view:
        render_agent_panel(project, note, action_result.actions, tools_dir)


if __name__ == "__main__":
    main()
