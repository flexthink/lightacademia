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
import os
import queue
import re
import signal
import shlex
import shutil
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import streamlit as st
import streamlit_extras.resizable_columns as resizable_columns_module
from tabulate import tabulate
from streamlit.components.v2.get_bidi_component_manager import get_bidi_component_manager
from streamlit.components.v2.manifest_scanner import ComponentConfig, ComponentManifest
from streamlit_extras.resizable_columns import resizable_columns

from lightacademia.actions import NoteAction, build_action_prompt, format_note_action, parse_note_actions
from lightacademia.agents import (
    AgentContext,
    AgentError,
    AgentProgress,
    AgentResult,
    AgentStopped,
    available_agent_models,
    default_agent,
)
from lightacademia.boards import (
    BoardAction,
    BoardFilter,
    NoteBoard,
    board_action_script,
    board_definition_hash,
    board_fetch_hash,
    board_fetch_script,
    build_board_action_prompt,
    build_board_prompt,
    parse_note_boards,
    script_action_hash,
    script_fetch_hash,
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
from lightacademia.gardener import (
    DEFAULT_FREQUENCY_MINUTES,
    GardenerResult,
    GardenerTask,
    add_gardener_task,
    build_gardener_followup_prompt,
    build_gardener_script_prompt,
    current_gardener_script,
    due_gardener_tasks,
    gardener_script_path,
    load_gardener_tasks,
    mark_gardener_finished,
    mark_gardener_process_started,
    mark_gardener_prepared,
    mark_gardener_started,
    parse_gardener_result,
    remove_gardener_task,
    request_gardener_run,
    reset_interrupted_gardener_tasks,
    update_gardener_action,
    update_gardener_frequency,
)
from lightacademia.markdown_preview import (
    ProjectDataframeError,
    ProjectFileLinkError,
    ProjectImageError,
    find_standalone_project_file_links,
    find_markdown_tables,
    find_standalone_dataframes,
    find_standalone_images,
    resolve_project_dataframe,
    resolve_project_link_path,
    resolve_project_image,
    relativize_project_links,
    rewrite_project_note_links,
)
from lightacademia.routing import WorkspaceRoute
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
    initialize_notebook,
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
NOTE_PANE_HEIGHT = 430
ICON_BUTTON_LABEL = ""
LOGO_PATH = Path("assets/logo.png")
THEME_CSS_PATH = Path("assets/theme.css")
COPY_IMAGE_BUTTON_CSS_PATH = Path("assets/copy_image_button.css")
COPY_TABLE_BUTTONS_CSS_PATH = Path("assets/copy_table_buttons.css")
WORKSPACE_TREE_CSS_PATH = Path("assets/workspace_tree.css")
WORKSPACE_TREE_JS_PATH = Path("assets/workspace_tree.js")
WORKSPACE_NAVIGATION_CSS_PATH = Path("assets/workspace_navigation.css")
WORKSPACE_NAVIGATION_JS_PATH = Path("assets/workspace_navigation.js")
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
.streamlit_code-editor,
.ace_editor {
  height: 100vh !important;
  min-height: 0 !important;
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


@lru_cache(maxsize=1)
def workspace_navigation_component():
    css = (
        WORKSPACE_NAVIGATION_CSS_PATH.read_text(encoding="utf-8")
        if WORKSPACE_NAVIGATION_CSS_PATH.exists()
        else ""
    )
    js = (
        WORKSPACE_NAVIGATION_JS_PATH.read_text(encoding="utf-8")
        if WORKSPACE_NAVIGATION_JS_PATH.exists()
        else ""
    )
    return st.components.v2.component(
        "light_academia_workspace_navigation",
        html='<div class="la-workspace-navigation"></div>',
        css=css,
        js=js,
    )


def workspace_navigation_route(
    *,
    mode: str,
    project_name: str,
    note_name: str | None,
    key: str,
    projects: list[dict[str, str]] | None = None,
    notes: list[dict[str, str]] | None = None,
) -> WorkspaceRoute:
    pending_route = st.session_state.pop("pending_hash_route", None)
    result = workspace_navigation_component()(
        key=key,
        data={
            "mode": mode,
            "project": project_name,
            "note": note_name or "",
            "projects": projects or [],
            "notes": notes or [],
            "pushRoute": pending_route or {},
        },
        default={"route": {"project": project_name, "note": note_name or ""}},
        on_route_change=lambda: None,
    )
    if mode != "projects":
        return WorkspaceRoute(project_name, note_name)
    if not isinstance(result, dict):
        return WorkspaceRoute(project_name, note_name)
    route = result.get("route")
    if not isinstance(route, dict):
        return WorkspaceRoute(project_name, note_name)
    selected_project = route.get("project")
    selected_note = route.get("note")
    return WorkspaceRoute(
        selected_project if isinstance(selected_project, str) and selected_project else project_name,
        selected_note if isinstance(selected_note, str) and selected_note else None,
    )


def workspace_tree_selection(
    tree: dict[str, object],
    selected: str | None,
    key: str,
    tree_id: str,
    *,
    labels: dict[str, str] | None = None,
    icons: dict[str, str] | None = None,
    pinned_last: tuple[str, ...] = (),
) -> str | None:
    result = workspace_tree_component()(
        key=key,
        data={
            "tree": tree,
            "selected": selected or "",
            "expandedDepth": 2,
            "treeId": tree_id,
            "labels": labels or {},
            "icons": icons or {},
            "pinnedLast": list(pinned_last),
        },
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
    codex_model: str | None
    agent_timeout_seconds: int


@dataclass
class AgentRunState:
    run_id: str
    project: Project
    note_name: str
    prompt: str
    tools_dir: Path
    agent: str
    codex_model: str | None
    agent_timeout_seconds: int
    before_status: set[str]
    progress: queue.Queue[AgentProgress]
    stop_requested: threading.Event
    thread: threading.Thread | None = None
    result: AgentResult | None = None
    response_message: str | None = None
    error: BaseException | None = None
    done: bool = False


@dataclass
class GardenerScriptRunState:
    run_id: str
    task: GardenerTask
    notebook_dir: Path
    project: Project
    note_name: str
    script_path: Path
    tools_dir: Path
    timeout_seconds: int
    stop_requested: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    process: subprocess.Popen[str] | None = None
    result: GardenerResult | None = None
    command: list[str] | None = None
    progress: queue.Queue[str] = field(default_factory=queue.Queue)
    stdout: str = ""
    stderr: str = ""
    error: BaseException | None = None
    done: bool = False


@dataclass(frozen=True)
class ToolDocument:
    name: str
    path: Path


@st.cache_resource
def agent_runs() -> dict[str, AgentRunState]:
    return {}


@st.cache_resource
def gardener_script_runs() -> dict[str, GardenerScriptRunState]:
    return {}


@st.cache_resource
def reset_gardener_tasks_at_startup(notebook_dir: str) -> int:
    """Run once per app process so abandoned work is not left busy forever."""
    return reset_interrupted_gardener_tasks(Path(notebook_dir))


@st.cache_resource
def agent_model_preferences() -> dict[str, str]:
    """Keep model choices across browser refreshes for this app process."""
    return {}


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--notebook", type=Path, default=DEFAULT_NOTEBOOK_DIR)
    parser.add_argument("--tools", type=Path, default=DEFAULT_TOOLS_DIR)
    parser.add_argument("--autocommit-seconds", type=int, default=DEFAULT_AUTOCOMMIT_SECONDS)
    parser.add_argument("--agent", choices=("codex", "claude"), default="codex")
    parser.add_argument("--codex-model", default=None, help="Model passed to the Codex SDK.")
    parser.add_argument("--agent-timeout-seconds", type=int, default=3600)
    args, _ = parser.parse_known_args()
    return AppConfig(
        notebook_dir=args.notebook.expanduser(),
        tools_dir=args.tools.expanduser(),
        autocommit_seconds=max(30, args.autocommit_seconds),
        agent=args.agent,
        codex_model=args.codex_model.strip() if args.codex_model and args.codex_model.strip() else None,
        agent_timeout_seconds=max(60, args.agent_timeout_seconds),
    )


@st.cache_data(ttl=300, show_spinner=False)
def selectable_agent_models(agent: str, codex_model: str | None) -> list[str] | None:
    """Cache the account lookup while leaving model support optional per agent."""
    return available_agent_models(agent, codex_model=codex_model)


def remember_selected_agent_model(agent: str, widget_key: str) -> None:
    selected_model = st.session_state.get(widget_key)
    if isinstance(selected_model, str) and selected_model:
        st.session_state.last_agent_model = selected_model
        agent_model_preferences()[agent] = selected_model


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
            queue_hash_route(project.name, HOME_NOTE)
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
            queue_hash_route(None)
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
                queue_hash_route(project.name, renamed.name)
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
        queue_hash_route(project.name, note.name)
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
            queue_hash_route(project.name, HOME_NOTE)
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
            link_markdown = f"[{note_display_name(target_note_name)}]({quote(target_note_name)})"
            insert_markdown_at_editor_cursor(note, link_markdown)
            st.session_state.source_visible = True
            st.rerun()
        except OSError as exc:
            st.error(f"Could not add link: {exc}")


@st.dialog("Add action", icon=":material/bolt:")
def add_action_dialog(project: Project, note) -> None:
    name = st.text_input(
        "Name",
        key=f"action_name_{project.name}_{note.name}",
    )
    instructions = st.text_area(
        "Instructions",
        height=180,
        key=f"action_instructions_{project.name}_{note.name}",
    )
    if st.button("Add action", type="primary", icon=":material/bolt:"):
        if not name.strip():
            st.warning("Enter an action name.")
            return
        if not instructions.strip():
            st.warning("Enter action instructions.")
            return
        try:
            save_editor_state(note)
            insert_markdown_at_editor_cursor(note, format_note_action(name, instructions))
            st.session_state.source_visible = True
            st.rerun()
        except OSError as exc:
            st.error(f"Could not add action: {exc}")


@st.dialog("Add to Gardener", icon=":material/local_florist:")
def add_gardener_action_dialog(notebook_dir: Path, project: Project, note, action: NoteAction) -> None:
    st.markdown(f"Schedule **{action.name}** from `{project.name}/{note.name}`.")
    frequency_minutes = st.number_input(
        "Run every (minutes)",
        min_value=1,
        value=DEFAULT_FREQUENCY_MINUTES,
        step=1,
        key=f"gardener_frequency_{project.name}_{note.name}_{action.line}",
    )
    if st.button("Add to Gardener", type="primary", icon=":material/local_florist:"):
        if active_agent_run() is not None:
            st.warning("Wait for the current Robot run to finish before adding this action.")
            return
        task = add_gardener_task(
            notebook_dir,
            project_name=project.name,
            note_name=note.name,
            action_name=action.name,
            instructions=action.instructions,
            frequency_minutes=int(frequency_minutes),
            action_line=action.line,
        )
        if not start_gardener_agent(
            task,
            project,
            note,
            build_gardener_script_prompt(task),
            mode="prepare",
        ):
            mark_gardener_finished(
                notebook_dir,
                task.id,
                status="error",
                result="Could not start Robot script preparation.",
            )
        st.rerun()


@st.dialog("Gardener", icon=":material/local_florist:")
def gardener_dialog(notebook_dir: Path) -> None:
    with st.container(key="gardener_dialog"):
        illustration_col, content_col = st.columns([0.35, 0.65], gap="medium")
        with illustration_col:
            with st.container(key="gardener_illustration"):
                st.image("assets/gardener.png", width="stretch")
        with content_col:
            tasks = load_gardener_tasks(notebook_dir)
            if not tasks:
                st.info("No actions have been added to Gardener.")
                return

            if st.button("Run all", icon=":material/play_arrow:", type="primary"):
                request_gardener_run(notebook_dir, {task.id for task in tasks})
                st.rerun()

            active_script_run = active_gardener_script_run()
            active_robot_run = active_agent_run()
            active_robot_gardener = st.session_state.get("active_gardener_agent")

            for task in tasks:
                with st.container(key=f"gardener_task_{task.id}"):
                    st.markdown(f"<p class='la-gardener-action'>{html.escape(task.action_name)}</p>", unsafe_allow_html=True)
                    st.caption(f"{task.project_name} / {task.note_name}")
                    project_path = (notebook_dir.resolve() / task.project_name).resolve()
                    cache_ready = project_path.is_dir() and current_gardener_script(project_path, task) is not None
                    cache_status = "ready" if cache_ready else "needs Robot review"
                    st.caption(f"Cached script: {cache_status}")
                    if task.last_run_at:
                        result = task.last_result or "No result recorded."
                        st.caption(f"Last run: {task.last_run_at} · {task.last_status or 'unknown'} · {result}")
                    else:
                        st.caption("Last run: not yet run")

                    is_running_script = active_script_run is not None and active_script_run.task.id == task.id
                    is_running_robot = (
                        isinstance(active_robot_gardener, dict)
                        and active_robot_gardener.get("task_id") == task.id
                        and active_robot_run is not None
                    )
                    frequency_col, run_col, stop_col, remove_col = st.columns([0.43, 0.25, 0.18, 0.14], vertical_alignment="bottom")
                    with frequency_col:
                        frequency_minutes = st.number_input(
                            "Every (minutes)",
                            min_value=1,
                            value=task.frequency_minutes,
                            step=1,
                            key=f"gardener_task_frequency_{task.id}",
                        )
                        if int(frequency_minutes) != task.frequency_minutes:
                            update_gardener_frequency(notebook_dir, task.id, int(frequency_minutes))
                            st.rerun()
                    with run_col:
                        if st.button(
                            "Run",
                            key=f"run_gardener_task_{task.id}",
                            icon=":material/play_arrow:",
                            width="stretch",
                            disabled=is_running_script or is_running_robot,
                        ):
                            request_gardener_run(notebook_dir, {task.id})
                            st.rerun()
                    with stop_col:
                        if is_running_script:
                            stopping = active_script_run.stop_requested.is_set()
                            if st.button(
                                "Stopping" if stopping else "Stop",
                                key=f"stop_gardener_script_{task.id}",
                                icon=":material/stop_circle:",
                                disabled=stopping,
                                width="stretch",
                            ):
                                stop_gardener_script_run(active_script_run)
                                st.rerun()
                        elif is_running_robot:
                            stopping = active_robot_run.stop_requested.is_set()
                            if st.button(
                                "Stopping" if stopping else "Stop",
                                key=f"stop_gardener_robot_{task.id}",
                                icon=":material/stop_circle:",
                                disabled=stopping,
                                width="stretch",
                            ):
                                active_robot_run.stop_requested.set()
                                st.rerun()
                    with remove_col:
                        if st.button(
                            ICON_BUTTON_LABEL,
                            key=f"remove_gardener_task_{task.id}",
                            icon=":material/delete:",
                            help="Remove from Gardener",
                            width="stretch",
                        ):
                            remove_gardener_task(notebook_dir, task.id)
                            st.rerun()


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


@st.dialog("Robot chat history", icon=":material/forum:")
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
        st.info(f"No robot chats recorded for {selected_date:%Y-%m-%d}.")
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
        "preview_source_key": None,
        "preview_source_content": "",
        "preview_source_project_dir": None,
        "preview_source_note_name": None,
        "last_agent_response": None,
        "last_agent_error": None,
        "last_fast_action_result": None,
        "pending_fast_action_agent_notice": None,
        "active_gardener_agent": None,
        "active_gardener_script_run_id": None,
        "gardener_progress_entries": [],
        "gardener_notifications": [],
        "last_gardener_result": None,
        "agent_running": False,
        "active_agent_run_id": None,
        "agent_progress_entries": [],
        "agent_progress_chars": 0,
        "requested_action": None,
        "queued_agent_prompt": None,
        "board_action_needs_app_rerun": False,
        "pending_fast_board_action": None,
        "agent_chat_revision": 0,
        "last_agent_model": None,
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
        "pending_hash_route": None,
        "navigation_revision": 0,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def queue_hash_route(project_name: str | None, note_name: str | None = None) -> None:
    st.session_state.pending_hash_route = (
        {"project": project_name, "note": note_name or ""}
        if project_name
        else None
    )
    st.session_state.navigation_revision += 1


def resolved_workspace_route(route: WorkspaceRoute, projects: list[Project]) -> WorkspaceRoute | None:
    project = next((item for item in projects if item.name == route.project), None)
    if project is None:
        return None
    note_names = [note.name for note in list_notes(project)]
    if not note_names:
        return WorkspaceRoute(project.name, None)
    note_name = route.note if route.note in note_names else None
    if note_name is None:
        note_name = HOME_NOTE if HOME_NOTE in note_names else note_names[0]
    return WorkspaceRoute(project.name, note_name)


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
    committed = git_commit_all(project.path, "Autosave checkpoint")
    st.session_state.last_commit_at = time.time()
    if committed:
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
    committed = git_commit_tools_text_files(tools_dir, "Autosave tools checkpoint")
    st.session_state.tool_last_commit_at = time.time()
    if committed:
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


def project_navigation_items(projects: list[Project]) -> list[dict[str, str]]:
    items = []
    for project in projects:
        notes = list_notes(project)
        note_names = [note.name for note in notes]
        default_note = HOME_NOTE if HOME_NOTE in note_names else (note_names[0] if note_names else "")
        items.append(
            {
                "name": project.name,
                "label": project.name,
                "defaultNote": default_note,
            }
        )
    return items


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
            options={"wrap": True, "fontSize": 14, "scrollPastEnd": 0.5},
            props={"scrollMargin": [15, 160, 0, 0]},
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
    hash_updated: bool = False,
) -> None:
    note_changed = (
        current_note_to_save is not None
        and save_editor_state(current_note_to_save)
    )
    if note_changed and git_commit_all(project.path, "Checkpoint before navigation"):
        st.session_state.last_commit_at = time.time()
    if next_project is not None:
        st.session_state.project_name = next_project
    if next_note is not None:
        st.session_state.note_name = next_note
    clear_history_revision()
    st.session_state.requested_action = None
    st.session_state.queued_agent_prompt = None
    if not hash_updated:
        queue_hash_route(
            next_project or project.name,
            next_note if next_note is not None else st.session_state.note_name,
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
            options={"wrap": True, "fontSize": 14, "scrollPastEnd": 0.5},
            props={"scrollMargin": [15, 160, 0, 0]},
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


@st.fragment
def render_preview_fragment() -> None:
    if st.session_state.pop("board_action_needs_app_rerun", False):
        st.rerun(scope="app")
    source_key = st.session_state.get("preview_source_key")
    source_content = st.session_state.get("preview_source_content", "")
    source_project_dir = st.session_state.get("preview_source_project_dir")
    source_note_name = st.session_state.get("preview_source_note_name") or ""
    allow_actions = bool(st.session_state.get("preview_allow_actions", True))
    if not source_key or not source_project_dir:
        return
    with st.container(key="preview_pane"):
        render_project_markdown(
            source_content,
            Path(source_project_dir),
            source_key,
            note_name=source_note_name,
            allow_actions=allow_actions,
        )


def render_project_markdown(
    markdown: str,
    project_dir: Path,
    source_key: str,
    note_name: str = "",
    allow_actions: bool = True,
) -> None:
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
        (board.line - 1, board.end_line, "board", board)
        for board in parse_note_boards(markdown).boards
    )
    events.extend(
        (image.start_line, image.end_line, "image", image)
        for image in find_standalone_images(markdown)
    )
    events.extend(
        (dataframe.start_line, dataframe.end_line, "dataframe", dataframe)
        for dataframe in find_standalone_dataframes(rendered_markdown)
    )
    events.extend(
        (file_link.start_line, file_link.end_line, "file", file_link)
        for file_link in find_standalone_project_file_links(rendered_markdown, project_dir)
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
            with st.container(horizontal=True, vertical_alignment="center", gap="small"):
                if st.button(
                    f"**Run:** {event.name}",
                    key=f"preview_action_{action_key}",
                    icon=":material/bolt:",
                    help="Select this action",
                ):
                    st.session_state.requested_action = event
                    st.session_state.agent_chat_revision += 1
                    st.rerun()
                if st.button(
                    ICON_BUTTON_LABEL,
                    key=f"add_gardener_action_{action_key}",
                    icon=":material/local_florist:",
                    help="Add to Gardener",
                ):
                    action_project = Project(project_dir.name, project_dir)
                    action_note = next(
                        (candidate for candidate in list_notes(action_project) if candidate.name == note_name),
                        None,
                    )
                    if action_note is not None:
                        add_gardener_action_dialog(get_config().notebook_dir, action_project, action_note, event)
            with st.expander("Action description", expanded=False):
                action_markdown = "".join(lines[start_line:end_line])
                if action_markdown.strip():
                    st.markdown(action_markdown)
        elif event_type == "board":
            render_note_board(
                event,
                project_dir,
                note_name,
                source_key,
                start_line,
                interactive=allow_actions,
            )
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
                    filters=event.filters,
                    dataframe_key=f"{source_key}:{start_line}:{event.target}",
                    annotation_error=event.annotation_error,
                )
        elif event_type == "file":
            try:
                file_path = resolve_project_link_path(project_dir, event.target)
                render_project_file_link(file_path, event.title, source_key, start_line)
            except (ProjectFileLinkError, OSError) as exc:
                st.warning(f"Could not open local file link: {exc}")
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


def render_project_file_link(file_path: Path, title: str, source_key: str, start_line: int) -> None:
    try:
        content = file_path.read_bytes()
    except OSError as exc:
        st.warning(f"Could not read `{file_path.name}`: {exc}")
        return
    mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    link_key = hashlib.sha256(
        f"{source_key}:file:{start_line}:{file_path}".encode("utf-8")
    ).hexdigest()[:16]
    with st.container(border=True):
        st.markdown(f"**{html.escape(title)}**")
        st.download_button(
            "Download",
            data=content,
            file_name=file_path.name,
            mime=mime_type,
            key=f"download_project_file_{link_key}",
            icon=":material/download:",
        )


def render_project_dataframe(
    dataframe_path: Path,
    project_dir: Path,
    columns: dict[str, str] | None = None,
    filters: tuple[BoardFilter, ...] = (),
    dataframe_key: str = "dataframe",
    annotation_error: str | None = None,
) -> None:
    project_root = project_dir.resolve()
    relative_path = dataframe_path.resolve().relative_to(project_root)
    try:
        dataframe = pd.read_csv(dataframe_path)
    except Exception as exc:
        st.warning(f"Could not read dataframe `{relative_path}`: {exc}")
        return
    dataframe = render_dataframe_filters(filters, dataframe, dataframe_key, source_name="Dataframe")
    if columns:
        dataframe = dataframe.rename(columns=columns)
    if annotation_error:
        st.warning(annotation_error)
    st.caption(str(relative_path))
    copy_key = hashlib.sha256(
        f"dataframe:{dataframe_path.resolve()}:{dataframe.to_csv(index=False)}".encode("utf-8")
    ).hexdigest()[:16]
    copy_table_buttons_component()(
        data={
            "markdownText": format_dataframe_for_plain_text(dataframe),
            "latexText": format_dataframe_for_latex(dataframe),
        },
        key=f"copy_dataframe_buttons_{copy_key}",
        height=42,
    )
    st.dataframe(dataframe, width="stretch", height=360)


def dataframe_copy_rows(dataframe: pd.DataFrame) -> list[list[object]]:
    normalized = dataframe.astype(object).where(pd.notna(dataframe), "")
    return normalized.values.tolist()


def format_dataframe_for_plain_text(dataframe: pd.DataFrame) -> str:
    return tabulate(
        dataframe_copy_rows(dataframe),
        headers=[str(column) for column in dataframe.columns],
        tablefmt="github",
        disable_numparse=True,
    )


def format_dataframe_for_latex(dataframe: pd.DataFrame) -> str:
    return tabulate(
        dataframe_copy_rows(dataframe),
        headers=[str(column) for column in dataframe.columns],
        tablefmt="latex_booktabs",
        disable_numparse=True,
    )


def queue_agent_prompt(prompt: str) -> None:
    st.session_state.queued_agent_prompt = prompt
    st.session_state.agent_chat_revision += 1


def board_row_values(dataframe: pd.DataFrame, row_index: int) -> dict[str, object]:
    values: dict[str, object] = {}
    for column, value in dataframe.iloc[row_index].to_dict().items():
        try:
            missing = bool(pd.isna(value))
        except (TypeError, ValueError):
            missing = False
        if missing:
            values[str(column)] = None
        elif hasattr(value, "item"):
            values[str(column)] = value.item()
        else:
            values[str(column)] = value
    return values


def render_board_filters(
    board_filters: tuple[BoardFilter, ...],
    dataframe: pd.DataFrame,
    board_key: str,
) -> pd.DataFrame:
    return render_dataframe_filters(board_filters, dataframe, board_key, source_name="Board")


def render_dataframe_filters(
    filters: tuple[BoardFilter, ...],
    dataframe: pd.DataFrame,
    filter_key: str,
    *,
    source_name: str,
) -> pd.DataFrame:
    if not filters:
        return dataframe

    columns_by_name = {str(column).casefold(): str(column) for column in dataframe.columns}
    configured_filters: list[tuple[BoardFilter, str]] = []
    for board_filter in filters:
        dataframe_column = columns_by_name.get(board_filter.column.casefold())
        if dataframe_column is None:
            st.warning(f"{source_name} filter column `{board_filter.column}` is not present in the CSV.")
            continue
        configured_filters.append((board_filter, dataframe_column))

    selected_values: list[tuple[BoardFilter, str, str | None]] = []
    for row_start in range(0, len(configured_filters), 4):
        filter_row = configured_filters[row_start : row_start + 4]
        columns = st.columns(len(filter_row))
        for offset, ((board_filter, dataframe_column), column) in enumerate(zip(filter_row, columns)):
            filter_index = row_start + offset
            widget_key = f"{source_name.casefold()}_filter_{filter_key}_{filter_index}"
            with column:
                if board_filter.filter_type == "dropdown":
                    options = sorted(
                        {
                            str(value)
                            for value in dataframe[dataframe_column].dropna().tolist()
                        },
                        key=str.casefold,
                    )
                    previous_selection = st.session_state.get(widget_key)
                    if (
                        isinstance(previous_selection, str)
                        and previous_selection
                        and previous_selection not in options
                    ):
                        options.insert(0, previous_selection)
                    selected = st.selectbox(
                        board_filter.column,
                        [None, *options],
                        format_func=lambda value: "All" if value is None else value,
                        key=widget_key,
                    )
                else:
                    selected = st.text_input(
                        board_filter.column,
                        key=widget_key,
                        placeholder=f"Filter {board_filter.column}...",
                    ).strip()
            selected_values.append((board_filter, dataframe_column, selected or None))

    filtered = dataframe
    for board_filter, dataframe_column, selected in selected_values:
        if selected is None:
            continue
        values = filtered[dataframe_column].astype("string")
        if board_filter.filter_type == "dropdown":
            mask = values.eq(selected).fillna(False)
        else:
            mask = values.str.contains(re.escape(selected), case=False, na=False, regex=True)
        filtered = filtered.loc[mask]

    if len(filtered.index) != len(dataframe.index):
        st.caption(f"Showing {len(filtered.index)} of {len(dataframe.index)} rows.")
    return filtered


def board_dataframe_column_order(board: NoteBoard, dataframe: pd.DataFrame) -> list[str]:
    available_columns = [str(column) for column in dataframe.columns]
    if not board.columns:
        return available_columns

    columns_by_name = {column.casefold(): column for column in available_columns}
    requested_columns: list[str] = []
    for requested_column in board.columns:
        dataframe_column = columns_by_name.get(requested_column.casefold())
        if dataframe_column is None:
            st.warning(f"Required board column `{requested_column}` is not present in the CSV.")
            continue
        requested_columns.append(dataframe_column)

    requested_names = set(requested_columns)
    return requested_columns + [
        column for column in available_columns if column not in requested_names
    ]


def current_fast_fetch_script(board: NoteBoard, project_dir: Path) -> Path | None:
    script_path = (project_dir.resolve() / board_fetch_script(board.name)).resolve()
    if not script_path.is_relative_to(project_dir.resolve()) or not script_path.is_file():
        return None
    try:
        script_hash = script_fetch_hash(script_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return None
    return script_path if script_hash == board_fetch_hash(board) else None


def current_fast_action_script(
    board: NoteBoard,
    action: BoardAction,
    project_dir: Path,
) -> Path | None:
    if not action.fast:
        return None
    project_root = project_dir.resolve()
    script_path = (project_root / board_action_script(board.name, action.name)).resolve()
    if not script_path.is_relative_to(project_root) or not script_path.is_file():
        return None
    try:
        script_hash = script_action_hash(script_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return None
    return script_path if script_hash == board_definition_hash(board) else None


def run_fast_board_fetch(board: NoteBoard, project_dir: Path, script_path: Path) -> None:
    config = get_config()
    relative_script = script_path.relative_to(project_dir.resolve())
    environment = os.environ.copy()
    environment["LIGHTACADEMIA_TOOLS"] = str(config.tools_dir.resolve())
    with st.spinner(f"Refreshing {board.name}..."):
        try:
            result = subprocess.run(
                [sys.executable, str(relative_script)],
                cwd=project_dir.resolve(),
                capture_output=True,
                text=True,
                timeout=config.agent_timeout_seconds,
                check=False,
                env=environment,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            st.session_state.last_agent_error = f"Fast fetch failed: {exc}"
            return

    if result.returncode != 0:
        details = (result.stderr or result.stdout or "No output.").strip()
        st.session_state.last_agent_error = (
            f"Fast fetch failed with exit code {result.returncode}:\n\n"
            f"```\n{details[-4000:]}\n```"
        )
        return

    csv_path = (project_dir.resolve() / board.data_file).resolve()
    if not csv_path.is_file():
        st.session_state.last_agent_error = (
            f"Fast fetch completed but did not create `{board.data_file}`."
        )
        return

    try:
        if git_commit_all(project_dir.resolve(), f"Refresh board {board.name}"):
            st.session_state.last_commit_at = time.time()
    except GitError as exc:
        st.session_state.last_agent_error = f"Fast fetch succeeded, but its changes could not be committed: {exc}"
        return
    st.session_state.last_agent_error = None


def run_fast_board_action(
    board: NoteBoard,
    action: BoardAction,
    row: dict[str, object],
    project_dir: Path,
    note_name: str,
    script_path: Path,
) -> bool:
    config = get_config()
    project_root = project_dir.resolve()
    relative_script = script_path.resolve().relative_to(project_root)
    environment = os.environ.copy()
    environment["LIGHTACADEMIA_TOOLS"] = str(config.tools_dir.resolve())
    row_json = json.dumps(row, ensure_ascii=False, default=str)
    with st.spinner(f"Running: {action.name}"):
        try:
            command = [
                sys.executable,
                str(relative_script),
                "--row-json",
                row_json,
                "--source-note",
                note_name,
                "--board-data-file",
                board.data_file,
            ]
            result = subprocess.run(
                command,
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=config.agent_timeout_seconds,
                check=False,
                env=environment,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            st.session_state.last_agent_error = f"Fast action `{action.name}` failed: {exc}"
            return False

    if result.returncode != 0:
        details = (result.stderr or result.stdout or "No output.").strip()
        st.session_state.last_agent_error = (
            f"Fast action `{action.name}` failed with exit code {result.returncode}:\n\n"
            f"```\n{details[-4000:]}\n```"
        )
        return False

    st.session_state.last_agent_error = None
    st.session_state.last_fast_action_result = {
        "action_name": action.name,
        "board_name": board.name,
        "project_dir": str(project_root),
        "note_name": note_name,
        "command": shlex.join(command),
        "output": result.stdout.strip(),
        "error_output": result.stderr.strip(),
    }
    return True


def refresh_board_after_action(
    board: NoteBoard,
    action: BoardAction,
    project_dir: Path,
    note_name: str,
) -> None:
    fast_script = (
        current_fast_fetch_script(board, project_dir)
        if board.fetch_mode == "fast"
        else None
    )
    if fast_script is not None:
        run_fast_board_fetch(board, project_dir, fast_script)
        return
    start_board_agent_command(
        build_board_prompt(board, note_name),
        project_dir,
        note_name,
        operation=f"board refresh after {action.name}",
    )


def run_pending_fast_board_action(
    board: NoteBoard,
    project_dir: Path,
    note_name: str,
) -> None:
    pending = st.session_state.get("pending_fast_board_action")
    if not isinstance(pending, dict):
        return
    if (
        pending.get("project_dir") != str(project_dir.resolve())
        or pending.get("note_name") != note_name
        or pending.get("board_name") != board.name
    ):
        return
    st.session_state.pending_fast_board_action = None
    action = pending.get("action")
    row = pending.get("row")
    script_path = pending.get("script_path")
    if (
        not isinstance(action, BoardAction)
        or not isinstance(row, dict)
        or not isinstance(script_path, str)
    ):
        st.session_state.last_agent_error = "Could not run fast action: invalid queued action."
        return
    completed = run_fast_board_action(
        board,
        action,
        row,
        project_dir,
        note_name,
        Path(script_path),
    )
    if completed and action.refresh:
        refresh_board_after_action(board, action, project_dir, note_name)


def handle_board_action_click(
    click_key: str,
    board: NoteBoard,
    action: BoardAction,
    dataframe: pd.DataFrame,
    project_dir: Path,
    note_name: str,
) -> None:
    click = st.session_state.get(click_key)
    if not click:
        return
    row_index = int(click["row"])
    if row_index < 0 or row_index >= len(dataframe.index):
        return
    row = board_row_values(dataframe, row_index)
    fast_script = current_fast_action_script(board, action, project_dir)
    if fast_script is not None:
        st.session_state.pending_fast_board_action = {
            "project_dir": str(project_dir.resolve()),
            "note_name": note_name,
            "board_name": board.name,
            "action": action,
            "row": row,
            "script_path": str(fast_script),
        }
        st.session_state.board_action_needs_app_rerun = True
        return
    prompt = build_board_action_prompt(
        board,
        action,
        row,
        note_name,
    )
    started = start_board_agent_command(
        prompt,
        project_dir,
        note_name,
        operation="board action",
    )
    if action.fast and started:
        run_id = st.session_state.get("active_agent_run_id")
        if isinstance(run_id, str):
            st.session_state.pending_fast_action_agent_notice = {
                "run_id": run_id,
                "action_name": action.name,
                "board_name": board.name,
                "project_dir": str(project_dir.resolve()),
                "note_name": note_name,
            }
    # ButtonColumn invokes this as a widget callback, where st.rerun() is a
    # no-op. The fragment run immediately following the callback performs it.
    st.session_state.board_action_needs_app_rerun = True


def render_note_board(
    board: NoteBoard,
    project_dir: Path,
    note_name: str,
    source_key: str,
    start_line: int,
    interactive: bool,
) -> None:
    board_key = hashlib.sha256(
        f"{project_dir.resolve()}:{note_name}:board:{start_line}:{board.name}".encode("utf-8")
    ).hexdigest()[:16]
    st.markdown(f"#### {board.name}")
    run_pending_fast_board_action(board, project_dir, note_name)
    controls_enabled = interactive and not bool(st.session_state.get("agent_running"))
    if controls_enabled and st.button(
        f"**Refresh:** {board.name}",
        key=f"refresh_board_{board_key}",
        icon=":material/refresh:",
        help="Refresh this board",
    ):
        fast_script = (
            current_fast_fetch_script(board, project_dir)
            if board.fetch_mode == "fast"
            else None
        )
        if fast_script is not None:
            run_fast_board_fetch(board, project_dir, fast_script)
        else:
            start_board_agent_command(
                build_board_prompt(board, note_name),
                project_dir,
                note_name,
                operation="board refresh",
            )
        st.rerun(scope="app")
    with st.expander("Fetch instructions", expanded=False):
        st.markdown(board.instructions)

    data_path = (project_dir.resolve() / board.data_file).resolve()
    if not data_path.is_relative_to(project_dir.resolve()):
        st.warning(f"Board data file must stay inside the project: `{board.data_file}`.")
        return
    st.caption(board.data_file)
    if not data_path.is_file():
        st.info("No board data yet. Refresh the board to fetch it.")
        return
    try:
        dataframe = pd.read_csv(data_path)
    except Exception as exc:
        st.warning(f"Could not read board data `{board.data_file}`: {exc}")
        return

    filtered_dataframe = render_board_filters(board.filters, dataframe, board_key)
    display_dataframe = filtered_dataframe.copy()
    column_config: dict[str, object] = {}
    column_order = board_dataframe_column_order(board, display_dataframe)
    if controls_enabled:
        for action_index, action in enumerate(board.actions):
            column_name = f"__board_action_{action_index}"
            click_key = f"board_action_click_{board_key}_{action_index}"
            display_dataframe[column_name] = [
                f":material/bolt: {action.name}"
            ] * len(display_dataframe.index)
            column_order.append(column_name)
            column_config[column_name] = st.column_config.ButtonColumn(
                action.name,
                width="small",
                help=action.instructions,
                type="secondary",
                on_click=handle_board_action_click,
                args=(click_key, board, action, filtered_dataframe, project_dir, note_name),
                key=click_key,
            )
    st.dataframe(
        display_dataframe,
        width="stretch",
        height=360,
        hide_index=True,
        column_order=column_order,
        column_config=column_config,
        key=f"board_dataframe_{board_key}",
    )


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
    st.session_state.preview_source_note_name = note.name
    st.session_state.preview_allow_actions = allow_actions
    render_preview_fragment()


def reload_note_from_disk() -> None:
    st.session_state.editor_revision += 1
    st.session_state.requested_action = None
    st.session_state.queued_agent_prompt = None
    clear_history_revision()
    st.session_state.preview_source_key = None
    st.session_state.preview_source_content = ""
    st.session_state.preview_source_project_dir = None
    st.session_state.preview_source_note_name = None


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
    codex_model: str | None,
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
        codex_model=codex_model,
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
    st.session_state.last_agent_response = None
    st.session_state.agent_progress_entries = []
    st.session_state.agent_progress_chars = 0
    logger.info("Starting agent run %s for project=%s note=%s", run_id, project.name, run_state.note_name)
    run_state.thread.start()
    return run_state


def start_board_agent_command(
    prompt: str,
    project_dir: Path,
    note_name: str,
    operation: str,
) -> bool:
    if active_agent_run() is not None:
        st.session_state.last_agent_error = f"Could not start {operation}: Robot is already running."
        return False
    project_path = project_dir.resolve()
    project = Project(project_path.name, project_path)
    note = next((item for item in list_notes(project) if item.name == note_name), None)
    if note is None:
        st.session_state.last_agent_error = f"Could not start {operation}: note `{note_name}` was not found."
        return False
    try:
        config = get_config()
        start_agent_command(
            project,
            note,
            prompt.strip(),
            config.tools_dir,
            config.agent,
            config.codex_model,
            config.agent_timeout_seconds,
        )
        st.session_state.last_agent_error = None
        st.session_state.agent_chat_revision += 1
        logger.info("Started %s for board in note=%s", operation, note_name)
        return True
    except (OSError, GitError, AgentError) as exc:
        st.session_state.last_agent_error = f"Could not start {operation}: {exc}"
        return False


def start_gardener_agent(
    task: GardenerTask,
    project: Project,
    note,
    prompt: str,
    *,
    mode: str,
) -> bool:
    if active_agent_run() is not None:
        return False
    try:
        config = get_config()
        run_state = start_agent_command(
            project,
            note,
            prompt,
            config.tools_dir,
            config.agent,
            config.codex_model,
            config.agent_timeout_seconds,
        )
    except (OSError, GitError, AgentError):
        return False
    st.session_state.active_gardener_agent = {
        "run_id": run_state.run_id,
        "task_id": task.id,
        "mode": mode,
    }
    if mode != "followup":
        st.session_state.last_gardener_result = None
    st.session_state.agent_chat_revision += 1
    phase = "preparing a cached script for" if mode in {"prepare", "rebuild-run"} else "continuing"
    queue_gardener_notification(
        f"Gardener is {phase} {task.action_name}",
        icon=":material/local_florist:",
    )
    logger.info("Started Gardener %s Robot run for task %s", mode, task.id)
    return True


def active_gardener_script_run() -> GardenerScriptRunState | None:
    run_id = st.session_state.get("active_gardener_script_run_id")
    if not isinstance(run_id, str):
        return None
    run_state = gardener_script_runs().get(run_id)
    if run_state is None:
        st.session_state.active_gardener_script_run_id = None
    return run_state


def queue_gardener_notification(message: str, *, icon: str) -> None:
    notifications = st.session_state.setdefault("gardener_notifications", [])
    notifications.append({"message": message, "icon": icon})


def record_gardener_result(
    task: GardenerTask,
    *,
    status: str,
    summary: str,
    details: object | None = None,
) -> None:
    st.session_state.last_gardener_result = {
        "task_id": task.id,
        "action_name": task.action_name,
        "project_name": task.project_name,
        "note_name": task.note_name,
        "status": status,
        "summary": summary,
        "details": details,
    }


def drain_gardener_progress(run_state: GardenerScriptRunState) -> None:
    entries = st.session_state.setdefault("gardener_progress_entries", [])
    while True:
        try:
            entries.append(run_state.progress.get_nowait())
        except queue.Empty:
            break
    while len(entries) > 250:
        entries.pop(0)


def start_gardener_script_run(
    task: GardenerTask,
    notebook_dir: Path,
    project: Project,
    note,
    script_path: Path,
    tools_dir: Path,
) -> GardenerScriptRunState:
    run_id = uuid.uuid4().hex
    run_state = GardenerScriptRunState(
        run_id=run_id,
        task=task,
        notebook_dir=notebook_dir,
        project=project,
        note_name=note.name,
        script_path=script_path,
        tools_dir=tools_dir,
        timeout_seconds=get_config().agent_timeout_seconds,
    )
    run_state.thread = threading.Thread(
        target=_gardener_script_worker,
        args=(run_state,),
        name=f"lightacademia-gardener-{run_id[:8]}",
        daemon=True,
    )
    gardener_script_runs()[run_id] = run_state
    st.session_state.active_gardener_script_run_id = run_id
    st.session_state.last_gardener_result = None
    st.session_state.gardener_progress_entries = [
        f"[gardener]\nStarting cached action: {task.action_name}",
        f"[command]\n{shlex.join([sys.executable, str(script_path.relative_to(project.path.resolve())), '--source-note', note.name])}",
    ]
    queue_gardener_notification(f"Gardener started: {task.action_name}", icon=":material/local_florist:")
    run_state.thread.start()
    return run_state


def _gardener_script_worker(run_state: GardenerScriptRunState) -> None:
    project_root = run_state.project.path.resolve()
    environment = os.environ.copy()
    environment["LIGHTACADEMIA_TOOLS"] = str(run_state.tools_dir.resolve())
    try:
        if run_state.stop_requested.is_set():
            run_state.result = GardenerResult(False, "stopped", "Cached action stopped by user.")
            return
        relative_script = run_state.script_path.resolve().relative_to(project_root)
        command = [
            sys.executable,
            str(relative_script),
            "--source-note",
            run_state.note_name,
        ]
        run_state.command = command
        process = subprocess.Popen(
            command,
            cwd=project_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            start_new_session=True,
        )
        run_state.process = process
        mark_gardener_process_started(run_state.notebook_dir, run_state.task.id, process.pid)
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []

        def read_stream(stream, lines: list[str], stream_name: str) -> None:
            for line in iter(stream.readline, ""):
                lines.append(line)
                text = line.rstrip()
                if text:
                    run_state.progress.put(f"[{stream_name}]\n{text}")
            stream.close()

        stdout_reader = threading.Thread(
            target=read_stream,
            args=(process.stdout, stdout_lines, "stdout"),
            daemon=True,
        )
        stderr_reader = threading.Thread(
            target=read_stream,
            args=(process.stderr, stderr_lines, "stderr"),
            daemon=True,
        )
        stdout_reader.start()
        stderr_reader.start()
        deadline = time.monotonic() + run_state.timeout_seconds
        while True:
            if run_state.stop_requested.is_set():
                _stop_gardener_process(process)
                returncode = process.wait()
                run_state.progress.put("[stopped]\nCached action stopped by user.")
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _stop_gardener_process(process)
                returncode = process.wait()
                run_state.progress.put("[error]\nCached action timed out and was stopped.")
                break
            try:
                returncode = process.wait(timeout=min(0.25, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
        stdout_reader.join()
        stderr_reader.join()
        run_state.stdout = "".join(stdout_lines).strip()
        run_state.stderr = "".join(stderr_lines).strip()
        if run_state.stop_requested.is_set():
            run_state.result = GardenerResult(False, "stopped", "Cached action stopped by user.")
        elif returncode != 0:
            run_state.result = GardenerResult(
                robot=True,
                status="error",
                summary=f"Cached script failed with exit code {returncode}.",
                details={"stdout": run_state.stdout[-4000:], "stderr": run_state.stderr[-4000:]},
            )
        else:
            try:
                run_state.result = parse_gardener_result(run_state.stdout)
            except ValueError as exc:
                run_state.result = GardenerResult(
                    robot=True,
                    status="error",
                    summary=str(exc),
                    details={"stdout": run_state.stdout[-4000:], "stderr": run_state.stderr[-4000:]},
                )
    except BaseException as exc:
        run_state.error = exc
    finally:
        run_state.process = None
        run_state.done = True


def _stop_gardener_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return


def stop_gardener_script_run(run_state: GardenerScriptRunState) -> None:
    run_state.stop_requested.set()
    if run_state.process is not None:
        _stop_gardener_process(run_state.process)


def resolve_gardener_target(
    notebook_dir: Path,
    task: GardenerTask,
) -> tuple[GardenerTask, Project, object] | None:
    notebook_root = notebook_dir.resolve()
    project_path = (notebook_root / task.project_name).resolve()
    if not project_path.is_relative_to(notebook_root) or not project_path.is_dir():
        mark_gardener_finished(
            notebook_dir,
            task.id,
            status="error",
            result=f"Project `{task.project_name}` was not found.",
        )
        return None
    project = Project(task.project_name, project_path)
    note = next((candidate for candidate in list_notes(project) if candidate.name == task.note_name), None)
    if note is None:
        mark_gardener_finished(
            notebook_dir,
            task.id,
            status="error",
            result=f"Note `{task.note_name}` was not found.",
        )
        return None
    actions = parse_note_actions(read_note(note)).actions
    action = next((candidate for candidate in actions if candidate.line == task.action_line), None)
    if action is None or action.name != task.action_name:
        action = next((candidate for candidate in actions if candidate.name == task.action_name), None)
    if action is None:
        mark_gardener_finished(
            notebook_dir,
            task.id,
            status="error",
            result=f"Action `{task.action_name}` was not found in `{task.note_name}`.",
        )
        return None
    if (
        action.name != task.action_name
        or action.instructions != task.instructions
        or action.line != task.action_line
    ):
        task = update_gardener_action(
            notebook_dir,
            task.id,
            action_name=action.name,
            instructions=action.instructions,
            action_line=action.line,
        ) or task
    return task, project, note


def finish_gardener_script_run(
    notebook_dir: Path,
    tools_dir: Path,
    run_state: GardenerScriptRunState,
) -> None:
    gardener_script_runs().pop(run_state.run_id, None)
    st.session_state.active_gardener_script_run_id = None
    if run_state.error is not None:
        mark_gardener_finished(
            notebook_dir,
            run_state.task.id,
            status="error",
            result=str(run_state.error),
        )
        record_gardener_result(
            run_state.task,
            status="error",
            summary=str(run_state.error),
        )
        queue_gardener_notification(
            f"Gardener failed: {run_state.task.action_name}",
            icon=":material/error:",
        )
        return
    result = run_state.result
    if result is None:
        mark_gardener_finished(
            notebook_dir,
            run_state.task.id,
            status="error",
            result="Cached script returned no result.",
        )
        record_gardener_result(
            run_state.task,
            status="error",
            summary="Cached script returned no result.",
        )
        queue_gardener_notification(
            f"Gardener failed: {run_state.task.action_name}",
            icon=":material/error:",
        )
        return
    if not result.robot:
        mark_gardener_finished(
            notebook_dir,
            run_state.task.id,
            status=result.status,
            result=result.summary,
        )
        record_gardener_result(
            run_state.task,
            status=result.status,
            summary=result.summary,
            details={"stdout": run_state.stdout, "stderr": run_state.stderr, "result": result.details},
        )
        queue_gardener_notification(
            f"Gardener finished: {run_state.task.action_name}",
            icon=":material/check_circle:",
        )
        return
    note = next(
        (candidate for candidate in list_notes(run_state.project) if candidate.name == run_state.note_name),
        None,
    )
    if note is None or not start_gardener_agent(
        run_state.task,
        run_state.project,
        note,
        build_gardener_followup_prompt(run_state.task, result),
        mode="followup",
    ):
        mark_gardener_finished(
            notebook_dir,
            run_state.task.id,
            status="error",
            result="Cached script requested Robot assistance, but the Robot could not start.",
        )
        record_gardener_result(
            run_state.task,
            status="error",
            summary="Cached script requested Robot assistance, but the Robot could not start.",
            details={"stdout": run_state.stdout, "stderr": run_state.stderr, "result": result.details},
        )
        queue_gardener_notification(
            f"Gardener needs attention: {run_state.task.action_name}",
            icon=":material/error:",
        )


@st.fragment(run_every="5s")
def render_gardener_scheduler(notebook_dir: Path, tools_dir: Path) -> None:
    script_run = active_gardener_script_run()
    if script_run is not None:
        if script_run.done:
            finish_gardener_script_run(notebook_dir, tools_dir, script_run)
            st.rerun(scope="app")
        return
    if active_agent_run() is not None:
        return
    due_tasks = due_gardener_tasks(notebook_dir)
    if not due_tasks:
        return

    target = resolve_gardener_target(notebook_dir, due_tasks[0])
    if target is None:
        return
    task, project, note = target
    mark_gardener_started(notebook_dir, task.id)
    script_path = current_gardener_script(project.path, task)
    if script_path is None:
        if not start_gardener_agent(
            task,
            project,
            note,
            build_gardener_script_prompt(task),
            mode="rebuild-run",
        ):
            mark_gardener_finished(
                notebook_dir,
                task.id,
                status="error",
                result="Could not start Robot script preparation.",
            )
    else:
        start_gardener_script_run(task, notebook_dir, project, note, script_path, tools_dir)
    st.rerun(scope="app")


def _agent_worker(run_state: AgentRunState) -> None:
    agent = default_agent(
        run_state.agent,
        timeout_seconds=run_state.agent_timeout_seconds,
        codex_model=run_state.codex_model,
    )
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
        normalize_project_markdown_links(run_state.project)
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


def normalize_project_markdown_links(project: Project) -> None:
    for note in list_notes(project):
        try:
            markdown = read_note(note)
            normalized = relativize_project_links(markdown, project.path)
        except OSError:
            continue
        if normalized != markdown:
            save_note(note, normalized)


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
        st.session_state.last_agent_error = "Robot stopped."
    else:
        st.session_state.last_agent_error = f"Could not run robot: {run_state.error}"
    gardener_agent = st.session_state.get("active_gardener_agent")
    if isinstance(gardener_agent, dict) and gardener_agent.get("run_id") == run_state.run_id:
        st.session_state.active_gardener_agent = None
        notebook_dir = get_config().notebook_dir
        task_id = gardener_agent.get("task_id")
        mode = gardener_agent.get("mode")
        task = next(
            (candidate for candidate in load_gardener_tasks(notebook_dir) if candidate.id == task_id),
            None,
        )
        if task is not None:
            if run_state.error is not None:
                status = "stopped" if isinstance(run_state.error, AgentStopped) else "error"
                summary = "Robot stopped by user." if status == "stopped" else str(run_state.error)
                mark_gardener_finished(
                    notebook_dir,
                    task.id,
                    status=status,
                    result=summary,
                )
                record_gardener_result(task, status=status, summary=summary)
                queue_gardener_notification(
                    f"Gardener {'stopped' if status == 'stopped' else 'failed'}: {task.action_name}",
                    icon=":material/stop_circle:" if status == "stopped" else ":material/error:",
                )
            elif mode == "followup":
                response = run_state.result.response if run_state.result is not None else "Completed."
                mark_gardener_finished(
                    notebook_dir,
                    task.id,
                    status="success",
                    result=response,
                )
                record_gardener_result(task, status="success", summary=response)
                queue_gardener_notification(
                    f"Gardener finished: {task.action_name}",
                    icon=":material/check_circle:",
                )
            else:
                target = resolve_gardener_target(notebook_dir, task)
                current_script = (
                    current_gardener_script(target[1].path, target[0])
                    if target is not None
                    else None
                )
                if current_script is None:
                    mark_gardener_finished(
                        notebook_dir,
                        task.id,
                        status="error",
                        result=f"Robot finished, but `{gardener_script_path(task)}` is missing or stale.",
                    )
                    record_gardener_result(
                        task,
                        status="error",
                        summary=f"Robot finished, but `{gardener_script_path(task)}` is missing or stale.",
                    )
                    queue_gardener_notification(
                        f"Gardener could not prepare: {task.action_name}",
                        icon=":material/error:",
                    )
                elif mode == "rebuild-run":
                    request_gardener_run(notebook_dir, {task.id})
                    queue_gardener_notification(
                        f"Gardener prepared {task.action_name}; starting cached action.",
                        icon=":material/local_florist:",
                    )
                else:
                    mark_gardener_prepared(
                        notebook_dir,
                        task.id,
                        f"Cached script ready: {gardener_script_path(task)}",
                    )
                    queue_gardener_notification(
                        f"Gardener prepared: {task.action_name}",
                        icon=":material/check_circle:",
                    )
    pending_notice = st.session_state.get("pending_fast_action_agent_notice")
    if isinstance(pending_notice, dict) and pending_notice.get("run_id") == run_state.run_id:
        st.session_state.pending_fast_action_agent_notice = None
        if run_state.error is None:
            st.session_state.last_fast_action_result = {
                **pending_notice,
                "command": "",
                "output": run_state.result.response if run_state.result is not None else "",
                "error_output": "",
            }
    reload_note_from_disk()


def render_fast_action_completion(project: Project, note) -> None:
    result = st.session_state.get("last_fast_action_result")
    if not isinstance(result, dict):
        return
    if (
        result.get("project_dir") != str(project.path.resolve())
        or result.get("note_name") != note.name
    ):
        return
    action_name = result.get("action_name")
    board_name = result.get("board_name")
    if not isinstance(action_name, str) or not isinstance(board_name, str):
        return
    st.success(
        f"**{action_name}** completed on **{board_name}**.",
        icon=":material/check_circle:",
    )
    command = result.get("command")
    output = result.get("output")
    error_output = result.get("error_output")
    if not any(isinstance(value, str) and value for value in (command, output, error_output)):
        return
    with st.expander("Command details", expanded=False):
        if isinstance(command, str) and command:
            st.code(command, language="bash")
        if isinstance(output, str) and output:
            st.code(output)
        if isinstance(error_output, str) and error_output:
            st.code(error_output)


def render_gardener_completion() -> None:
    result = st.session_state.get("last_gardener_result")
    if not isinstance(result, dict):
        return
    action_name = result.get("action_name")
    summary = result.get("summary")
    status = result.get("status")
    if not isinstance(action_name, str) or not isinstance(summary, str) or not summary:
        return

    summary_col, dismiss_col = st.columns([0.94, 0.06], vertical_alignment="top")
    with summary_col:
        if status == "success":
            st.success(f"Gardener completed **{action_name}**.", icon=":material/check_circle:")
        else:
            st.warning(f"Gardener finished **{action_name}** with status `{status or 'unknown'}`.")
        st.markdown(summary)
    with dismiss_col:
        if st.button(
            ICON_BUTTON_LABEL,
            key="dismiss_gardener_result",
            help="Dismiss Gardener result",
            icon=":material/close:",
        ):
            st.session_state.last_gardener_result = None
            st.rerun()

    details = result.get("details")
    if details is not None:
        with st.expander("Gardener details", expanded=False):
            if isinstance(details, str):
                st.code(details)
            else:
                st.code(json.dumps(details, ensure_ascii=False, indent=2, default=str), language="json")


def render_agent_completion() -> None:
    response = st.session_state.get("last_agent_response")
    if not isinstance(response, str) or not response:
        return
    summary_col, dismiss_col = st.columns([0.94, 0.06], vertical_alignment="center")
    with summary_col:
        st.success("Robot run completed.", icon=":material/check_circle:")
    with dismiss_col:
        if st.button(
            ICON_BUTTON_LABEL,
            key="dismiss_agent_summary",
            help="Dismiss robot summary",
            icon=":material/close:",
        ):
            st.session_state.last_agent_response = None
            st.rerun()
    with st.expander("Robot details", expanded=False):
        st.markdown(response)


def render_gardener_notifications() -> None:
    notifications = st.session_state.get("gardener_notifications")
    if not isinstance(notifications, list):
        return
    st.session_state.gardener_notifications = []
    for notification in notifications:
        if not isinstance(notification, dict):
            continue
        message = notification.get("message")
        icon = notification.get("icon")
        if isinstance(message, str) and message:
            st.toast(message, icon=icon if isinstance(icon, str) else None)


def render_agent_panel(project: Project, note, actions: tuple[NoteAction, ...], tools_dir: Path) -> None:
    run_state = active_agent_run()
    agent_chat_revision = st.session_state.get("agent_chat_revision", 0)
    with st.container(key="agent_panel"):
        with st.expander(
            "Robot chat",
            expanded=True,
            key=f"agent_chat_expanded_{agent_chat_revision}",
            on_change="rerun",
        ):
            if run_state is not None:
                render_agent_progress_panel()
            elif active_gardener_script_run() is not None:
                render_gardener_script_progress_panel()
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


@st.fragment(run_every="250ms")
def render_gardener_script_progress_panel() -> None:
    run_state = active_gardener_script_run()
    if run_state is None:
        return
    drain_gardener_progress(run_state)
    entries = st.session_state.get("gardener_progress_entries", [])
    with st.status(f"🌿 Gardener is running {run_state.task.action_name}...", expanded=True, state="running"):
        stop_col, detail_col = st.columns([0.2, 0.8], vertical_alignment="center")
        with stop_col:
            if st.button(
                "Stopping" if run_state.stop_requested.is_set() else "Stop",
                key=f"stop_gardener_progress_{run_state.run_id}",
                icon=":material/stop_circle:",
                disabled=run_state.stop_requested.is_set(),
            ):
                stop_gardener_script_run(run_state)
                st.rerun()
        with detail_col:
            st.caption("Running the cached action script")
        st.container(height=220, border=False).code(
            "\n\n".join(entries) if entries else "Waiting for cached action output...",
            language=None,
        )
    if run_state.done:
        finish_gardener_script_run(get_config().notebook_dir, get_config().tools_dir, run_state)
        st.rerun(scope="app")


def render_agent_form(project: Project, note, actions: tuple[NoteAction, ...], tools_dir: Path) -> None:
    prompt_key = f"agent_prompt_{note.name}_{st.session_state.editor_revision}"
    queued_prompt = st.session_state.pop("queued_agent_prompt", None)
    if isinstance(queued_prompt, str) and queued_prompt.strip():
        st.session_state[action_selector_key(note)] = None
        st.session_state[prompt_key] = queued_prompt

    config = get_config()
    selected_model = config.codex_model
    model_error: str | None = None
    try:
        model_options = selectable_agent_models(config.agent, config.codex_model)
    except AgentError as exc:
        model_options = None
        model_error = str(exc)

    if model_options:
        last_model = agent_model_preferences().get(config.agent) or st.session_state.get(
            "last_agent_model"
        )
        default_model = last_model or config.codex_model
        if default_model and default_model not in model_options:
            model_options = [default_model, *model_options]
        model_widget_key = f"agent_model_{config.agent}"
        seeded_from_command_line = last_model is None and bool(config.codex_model)
        if seeded_from_command_line:
            # Persist the command-line choice even if Streamlit later discards the widget.
            st.session_state.last_agent_model = config.codex_model
            default_model = config.codex_model
        if (
            seeded_from_command_line
            or model_widget_key not in st.session_state
            or st.session_state[model_widget_key] not in model_options
        ):
            if default_model:
                st.session_state[model_widget_key] = default_model
            else:
                st.session_state[model_widget_key] = model_options[0]
        selected_model = st.selectbox(
            "Model",
            model_options,
            key=model_widget_key,
            on_change=remember_selected_agent_model,
            args=(config.agent, model_widget_key),
        )
    elif model_error:
        st.caption(f"Model selection is unavailable: {model_error}")
    elif model_options is not None:
        st.caption("Model selection is unavailable: Codex returned no models for this account.")

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
        prompt = st.text_area("Message", height=120, key=prompt_key, label_visibility="collapsed")
    submitted = st.button(
        "Run robot",
        key=f"run_agent_{note.name}_{st.session_state.editor_revision}",
    )
    if submitted and (prompt.strip() or selected_action is not None):
        agent_prompt = (
            build_action_prompt(selected_action, note.name, prompt)
            if selected_action is not None
            else prompt.strip()
        )
        try:
            start_agent_command(
                project,
                note,
                agent_prompt,
                tools_dir,
                config.agent,
                selected_model,
                config.agent_timeout_seconds,
            )
            st.session_state.last_agent_model = selected_model
            if selected_model:
                agent_model_preferences()[config.agent] = selected_model
            st.session_state.last_agent_error = None
        except (OSError, GitError, AgentError) as exc:
            st.session_state.last_agent_error = f"Could not start robot: {exc}"
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
        initialize_notebook(notebook_dir)
        ensure_tools_dir(tools_dir)
        ensure_tools_git_repo(tools_dir)
        reset_gardener_tasks_at_startup(str(notebook_dir.resolve()))
        projects = list_projects(notebook_dir)
    except (OSError, GitError) as exc:
        st.error(f"Could not open workspace folders: {exc}")
        return
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
            st.session_state.requested_action = None
            st.session_state.queued_agent_prompt = None
            st.session_state.workspace_view = "tools"
            st.rerun()
        st.divider()

        project_header, project_new, project_archive = st.columns([0.66, 0.17, 0.17], vertical_alignment="bottom")
        with project_header:
            selected_route = workspace_navigation_route(
                mode="projects",
                project_name=project.name,
                note_name=note_before_project_navigation.name if note_before_project_navigation else None,
                key=f"project_navigation_{st.session_state.navigation_revision}",
                projects=project_navigation_items(projects),
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
        resolved_route = resolved_workspace_route(selected_route, projects)
        if resolved_route is None:
            queue_hash_route(
                project.name,
                note_before_project_navigation.name if note_before_project_navigation else None,
            )
            st.rerun()
        if (
            resolved_route.project != project.name
            or (
                resolved_route.note is not None
                and note_before_project_navigation is not None
                and resolved_route.note != note_before_project_navigation.name
            )
        ):
            commit_before_navigation(
                project,
                current_note_to_save=note_before_project_navigation,
                next_project=resolved_route.project,
                next_note=resolved_route.note,
                hash_updated=True,
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

    with st.sidebar:
        st.markdown("### Notes")
        selected_route = workspace_navigation_route(
            mode="notes",
            project_name=project.name,
            note_name=note.name,
            key=f"note_navigation_{project.path.resolve()}_{st.session_state.navigation_revision}",
            notes=[
                {
                    "name": item.name,
                    "label": note_display_name(item.name),
                    "icon": "wrench" if item.name.casefold() == "skill.md" else "note",
                }
                for item in notes
            ],
        )
        resolved_route = resolved_workspace_route(selected_route, projects)
        if resolved_route is None or resolved_route.project != project.name:
            if resolved_route is None:
                queue_hash_route(project.name, note.name)
                st.rerun()
            commit_before_navigation(
                project,
                current_note_to_save=note,
                next_project=resolved_route.project,
                next_note=resolved_route.note,
                hash_updated=True,
            )
        if resolved_route.note and resolved_route.note != note.name:
            commit_before_navigation(
                project,
                current_note_to_save=note,
                next_note=resolved_route.note,
                hash_updated=True,
            )

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
                help="Robot chat history",
                icon=":material/forum:",
            ):
                chat_history_dialog(project)
            if st.button(
                ICON_BUTTON_LABEL,
                key="open_gardener",
                help="Gardener",
                icon=":material/local_florist:",
            ):
                gardener_dialog(notebook_dir)
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
                key="open_add_action",
                help="Add action",
                icon=":material/bolt:",
                disabled=is_history_view,
            ):
                add_action_dialog(project, note)
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
    board_result = parse_note_boards(action_source)
    if not is_history_view:
        apply_requested_action(note, action_result.actions)
    for error in action_result.errors:
        st.warning(f"Action block at line {error.line}: {error.message}")
    for error in board_result.errors:
        st.warning(f"Board block at line {error.line}: {error.message}")

    if st.session_state.last_sync_error:
        st.warning(st.session_state.last_sync_error)
    elif st.session_state.last_sync_message:
        st.info(st.session_state.last_sync_message)

    if st.session_state.last_agent_error:
        error_col, dismiss_col = st.columns([0.94, 0.06], vertical_alignment="top")
        with error_col:
            st.error(st.session_state.last_agent_error)
        with dismiss_col:
            if st.button(
                ICON_BUTTON_LABEL,
                key="dismiss_agent_error",
                help="Dismiss robot error",
                icon=":material/close:",
            ):
                st.session_state.last_agent_error = None
                st.rerun()
    render_fast_action_completion(project, note)
    has_agent_completion = isinstance(st.session_state.get("last_gardener_result"), dict) or bool(
        st.session_state.get("last_agent_response")
    )
    if has_agent_completion:
        with st.container(key="agent_completion"):
            render_gardener_completion()
            render_agent_completion()
    render_gardener_notifications()

    if not is_history_view:
        render_agent_panel(project, note, action_result.actions, tools_dir)
    render_gardener_scheduler(notebook_dir, tools_dir)


if __name__ == "__main__":
    main()
