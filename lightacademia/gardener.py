from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import yaml


GARDENER_FILE = ".gardener.yaml"
DEFAULT_FREQUENCY_MINUTES = 60
GARDENER_HASH_MARKER = "lightacademia-gardener-action-sha256"


@dataclass(frozen=True)
class GardenerTask:
    id: str
    project_name: str
    note_name: str
    action_name: str
    instructions: str
    frequency_minutes: int
    last_started_at: str | None = None
    last_run_at: str | None = None
    last_status: str | None = None
    last_result: str | None = None
    action_line: int = 0
    active_pid: int | None = None


@dataclass(frozen=True)
class GardenerResult:
    robot: bool
    status: str
    summary: str
    details: object = None


def gardener_path(notebook_dir: Path) -> Path:
    return notebook_dir.resolve() / GARDENER_FILE


def load_gardener_tasks(notebook_dir: Path) -> list[GardenerTask]:
    path = gardener_path(notebook_dir)
    if not path.is_file():
        return []
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    raw_tasks = payload.get("tasks", []) if isinstance(payload, dict) else []
    tasks: list[GardenerTask] = []
    for raw_task in raw_tasks:
        if not isinstance(raw_task, dict):
            continue
        try:
            task = GardenerTask(
                id=str(raw_task["id"]),
                project_name=str(raw_task["project_name"]),
                note_name=str(raw_task["note_name"]),
                action_name=str(raw_task["action_name"]),
                instructions=str(raw_task["instructions"]),
                frequency_minutes=max(1, int(raw_task.get("frequency_minutes", DEFAULT_FREQUENCY_MINUTES))),
                last_started_at=_optional_string(raw_task.get("last_started_at")),
                last_run_at=_optional_string(raw_task.get("last_run_at")),
                last_status=_optional_string(raw_task.get("last_status")),
                last_result=_optional_string(raw_task.get("last_result")),
                action_line=max(0, int(raw_task.get("action_line", 0))),
                active_pid=_optional_positive_int(raw_task.get("active_pid")),
            )
        except (KeyError, TypeError, ValueError):
            continue
        tasks.append(task)
    return tasks


def save_gardener_tasks(notebook_dir: Path, tasks: list[GardenerTask]) -> None:
    path = gardener_path(notebook_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"tasks": [asdict(task) for task in tasks]}
    temporary_path = path.with_suffix(".tmp")
    temporary_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    temporary_path.replace(path)


def add_gardener_task(
    notebook_dir: Path,
    *,
    project_name: str,
    note_name: str,
    action_name: str,
    instructions: str,
    frequency_minutes: int,
    action_line: int = 0,
) -> GardenerTask:
    task = GardenerTask(
        id=uuid4().hex,
        project_name=project_name,
        note_name=note_name,
        action_name=action_name,
        instructions=instructions,
        frequency_minutes=max(1, frequency_minutes),
        last_started_at=datetime.now(timezone.utc).isoformat(),
        action_line=max(0, action_line),
    )
    tasks = load_gardener_tasks(notebook_dir)
    tasks.append(task)
    save_gardener_tasks(notebook_dir, tasks)
    return task


def remove_gardener_task(notebook_dir: Path, task_id: str) -> None:
    save_gardener_tasks(
        notebook_dir,
        [task for task in load_gardener_tasks(notebook_dir) if task.id != task_id],
    )


def update_gardener_frequency(notebook_dir: Path, task_id: str, frequency_minutes: int) -> None:
    updated = []
    for task in load_gardener_tasks(notebook_dir):
        if task.id == task_id:
            task = GardenerTask(**{**asdict(task), "frequency_minutes": max(1, frequency_minutes)})
        updated.append(task)
    save_gardener_tasks(notebook_dir, updated)


def update_gardener_action(
    notebook_dir: Path,
    task_id: str,
    *,
    action_name: str,
    instructions: str,
    action_line: int,
) -> GardenerTask | None:
    updated_task = None
    updated = []
    for task in load_gardener_tasks(notebook_dir):
        if task.id == task_id:
            task = GardenerTask(
                **{
                    **asdict(task),
                    "action_name": action_name,
                    "instructions": instructions,
                    "action_line": max(0, action_line),
                }
            )
            updated_task = task
        updated.append(task)
    save_gardener_tasks(notebook_dir, updated)
    return updated_task


def request_gardener_run(notebook_dir: Path, task_ids: set[str]) -> None:
    updated = []
    for task in load_gardener_tasks(notebook_dir):
        if task.id in task_ids:
            task = GardenerTask(**{**asdict(task), "last_started_at": None})
        updated.append(task)
    save_gardener_tasks(notebook_dir, updated)


def reset_interrupted_gardener_tasks(notebook_dir: Path) -> int:
    """Make tasks left running by a previous app process runnable again."""
    reset_count = 0
    updated = []
    for task in load_gardener_tasks(notebook_dir):
        if task.last_status in {"busy", "running"}:
            _stop_tracked_gardener_process(task.active_pid, gardener_script_path(task))
            task = GardenerTask(
                **{
                    **asdict(task),
                    "last_started_at": None,
                    "last_status": "ready",
                    "last_result": "Reset after Light Academia restarted.",
                    "active_pid": None,
                }
            )
            reset_count += 1
        updated.append(task)
    if reset_count:
        save_gardener_tasks(notebook_dir, updated)
    return reset_count


def due_gardener_tasks(notebook_dir: Path, now: datetime | None = None) -> list[GardenerTask]:
    current_time = now or datetime.now(timezone.utc)
    due = []
    for task in load_gardener_tasks(notebook_dir):
        last_started = _parse_timestamp(task.last_started_at)
        if last_started is None or current_time - last_started >= timedelta(minutes=task.frequency_minutes):
            due.append(task)
    return due


def mark_gardener_started(notebook_dir: Path, task_id: str, now: datetime | None = None) -> None:
    timestamp = (now or datetime.now(timezone.utc)).isoformat()
    _replace_task(notebook_dir, task_id, last_started_at=timestamp, last_status="running", last_result=None, active_pid=None)


def mark_gardener_process_started(notebook_dir: Path, task_id: str, process_id: int) -> None:
    _replace_task(notebook_dir, task_id, active_pid=process_id)


def mark_gardener_finished(
    notebook_dir: Path,
    task_id: str,
    *,
    status: str,
    result: str,
    now: datetime | None = None,
) -> None:
    timestamp = (now or datetime.now(timezone.utc)).isoformat()
    _replace_task(
        notebook_dir,
        task_id,
        last_run_at=timestamp,
        last_status=status,
        last_result=_short_result(result),
        active_pid=None,
    )


def mark_gardener_prepared(notebook_dir: Path, task_id: str, result: str) -> None:
    _replace_task(
        notebook_dir,
        task_id,
        last_status="prepared",
        last_result=_short_result(result),
    )


def gardener_definition_hash(task: GardenerTask) -> str:
    payload = {
        "project_name": task.project_name,
        "note_name": task.note_name,
        "action_name": task.action_name,
        "instructions": task.instructions,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def gardener_hash_comment(task: GardenerTask) -> str:
    return f"# {GARDENER_HASH_MARKER}: {gardener_definition_hash(task)}"


def gardener_script_path(task: GardenerTask) -> str:
    note = _normalize_name(Path(task.note_name).stem)
    action = _normalize_name(task.action_name)
    return f"code/gardener-{note}-{action}-{task.id[:8]}.py"


def script_gardener_hash(script: str) -> str | None:
    match = re.search(
        rf"(?m)^\s*#\s*{re.escape(GARDENER_HASH_MARKER)}:\s*([0-9a-fA-F]{{64}})\s*$",
        script,
    )
    return match.group(1).lower() if match else None


def current_gardener_script(project_dir: Path, task: GardenerTask) -> Path | None:
    project_root = project_dir.resolve()
    script_path = (project_root / gardener_script_path(task)).resolve()
    if not script_path.is_relative_to(project_root) or not script_path.is_file():
        return None
    try:
        script_hash = script_gardener_hash(script_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return None
    return script_path if script_hash == gardener_definition_hash(task) else None


def build_gardener_script_prompt(task: GardenerTask) -> str:
    return (
        "Prepare a cached Gardener action script. Do not execute the action now.\n\n"
        f"Source note: {task.note_name}\n"
        f"Action name: {task.action_name}\n"
        f"Script path: {gardener_script_path(task)}\n\n"
        "Action instructions:\n"
        f"{task.instructions}\n\n"
        "Implementation requirements:\n"
        "- Review the action and automate as much of it as can be done deterministically.\n"
        f"- Create or update `{gardener_script_path(task)}` inside the selected project.\n"
        "- Include this exact marked comment in the script:\n"
        f"  `{gardener_hash_comment(task)}`\n"
        "- Run non-interactively with Python from the project root and accept "
        "`--source-note <name>`.\n"
        "- Locate researcher tools through `LIGHTACADEMIA_TOOLS`; never hardcode the tools path.\n"
        "- Write diagnostics to stderr. Write exactly one JSON object to stdout.\n"
        "- The JSON object must contain `robot` (boolean), `status` (string), `summary` "
        "(short string), and `details` (any JSON value).\n"
        "- Set `robot` to true only when the Robot must inspect the result or complete work that "
        "cannot be handled safely by the script. Include all useful context in `details`.\n"
        "- Set `robot` to false when the action is complete without further reasoning.\n"
        "- Do not run the generated script or perform the action during this preparation run."
    )


def build_gardener_followup_prompt(task: GardenerTask, result: GardenerResult) -> str:
    result_json = json.dumps(
        {
            "robot": result.robot,
            "status": result.status,
            "summary": result.summary,
            "details": result.details,
        },
        ensure_ascii=False,
        indent=2,
        default=str,
    )
    return (
        "Complete a Gardener action after its cached script requested Robot assistance.\n\n"
        f"Source note: {task.note_name}\n"
        f"Action name: {task.action_name}\n\n"
        "Original action instructions:\n"
        f"{task.instructions}\n\n"
        "Cached script result:\n"
        f"```json\n{result_json}\n```\n\n"
        "Inspect the result and perform only the remaining work needed to complete the action. "
        "Use the supplied details instead of repeating successful computation."
    )


def parse_gardener_result(stdout: str) -> GardenerResult:
    try:
        payload = json.loads(stdout.strip())
    except json.JSONDecodeError as exc:
        raise ValueError("Gardener script stdout was not a JSON object.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Gardener script stdout must be a JSON object.")
    robot = payload.get("robot")
    status = payload.get("status")
    summary = payload.get("summary")
    if not isinstance(robot, bool):
        raise ValueError("Gardener result field `robot` must be a boolean.")
    if not isinstance(status, str) or not status.strip():
        raise ValueError("Gardener result field `status` must be a non-empty string.")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("Gardener result field `summary` must be a non-empty string.")
    return GardenerResult(robot, status.strip(), summary.strip(), payload.get("details"))


def _replace_task(notebook_dir: Path, task_id: str, **changes: object) -> None:
    updated = []
    for task in load_gardener_tasks(notebook_dir):
        if task.id == task_id:
            task = GardenerTask(**{**asdict(task), **changes})
        updated.append(task)
    save_gardener_tasks(notebook_dir, updated)


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_positive_int(value: object) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _stop_tracked_gardener_process(process_id: int | None, expected_script: str) -> None:
    """Terminate only a verified, dedicated Gardener process group."""
    if process_id is None:
        return
    try:
        command = subprocess.run(
            ["ps", "-o", "command=", "-p", str(process_id)],
            capture_output=True,
            check=False,
            text=True,
        ).stdout.strip()
        if (
            command
            and expected_script in command
            and os.getpgid(process_id) == process_id
        ):
            os.killpg(process_id, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        return


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    return timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)


def _short_result(value: str, limit: int = 240) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3]}..."


def _normalize_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "-", value.strip().lower()).strip("-")
    return normalized or "action"
