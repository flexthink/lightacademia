from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, unquote


ROUTE_PREFIX = "#/project/"


@dataclass(frozen=True)
class WorkspaceRoute:
    project: str
    note: str | None = None


def workspace_hash(project: str, note: str | None = None) -> str:
    route = f"{ROUTE_PREFIX}{quote(project, safe='')}"
    if note:
        route += f"/note/{quote(note, safe='')}"
    return route


def parse_workspace_hash(value: str) -> WorkspaceRoute | None:
    if not value.startswith(ROUTE_PREFIX):
        return None
    remainder = value[len(ROUTE_PREFIX) :]
    encoded_project, separator, encoded_note = remainder.partition("/note/")
    if not encoded_project:
        return None
    project = unquote(encoded_project)
    note = unquote(encoded_note) if separator and encoded_note else None
    return WorkspaceRoute(project=project, note=note)
