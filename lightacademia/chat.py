from __future__ import annotations

from datetime import date, datetime
from pathlib import Path


def today_chat_log(project_dir: Path) -> Path:
    chats_dir = project_dir / "chats"
    chats_dir.mkdir(exist_ok=True)
    return chats_dir / f"{datetime.now().strftime('%Y-%m-%d')}.md"


def chat_log_path(project_dir: Path, chat_date: date) -> Path:
    return project_dir / "chats" / f"{chat_date:%Y-%m-%d}.md"


def list_chat_log_dates(project_dir: Path) -> list[date]:
    chats_dir = project_dir / "chats"
    if not chats_dir.exists():
        return []

    dates = []
    for path in chats_dir.glob("*.md"):
        try:
            dates.append(date.fromisoformat(path.stem))
        except ValueError:
            continue
    return sorted(dates, reverse=True)


def read_chat_log(project_dir: Path, chat_date: date) -> str:
    path = chat_log_path(project_dir, chat_date)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def markdown_list(items: list[str], empty_text: str = "_None recorded._") -> str:
    if not items:
        return empty_text
    return "\n".join(f"- {item}" for item in items)


def append_chat_entry(
    project_dir: Path,
    user_prompt: str,
    agent_response: str,
    agent_name: str = "Agent",
    tool_actions: list[str] | None = None,
    file_changes: list[str] | None = None,
) -> Path:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    log_path = today_chat_log(project_dir)
    entry = (
        f"\n## {now}\n\n"
        f"Agent: {agent_name}\n\n"
        f"### User\n\n{user_prompt.strip()}\n\n"
        f"### Agent\n\n{agent_response.strip()}\n\n"
        f"### Tool Actions\n\n{markdown_list(tool_actions or [])}\n\n"
        f"### File Changes\n\n{markdown_list(file_changes or [])}\n"
    )
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(entry)
    return log_path
