"""Provider-independent activity updates and a bounded, immutable stream reducer.

Providers translate their events to ActivityUpdate. The reducer owns item identity,
delta assembly, and lifecycle transitions; views only consume Activity snapshots.
This module deliberately has no SDK or Streamlit dependency.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal

ActivityKind = Literal["reasoning", "message", "command", "tool", "files", "search", "plan", "error", "log", "lifecycle"]
ActivityState = Literal["running", "complete", "error"]


@dataclass(frozen=True)
class ActivityUpdate:
    item_id: str | None
    kind: ActivityKind
    label: str
    state: ActivityState = "running"
    content: dict[str, str] = field(default_factory=dict)
    append: bool = False


@dataclass(frozen=True)
class AgentProgress:
    event_type: str
    text: str
    activity: ActivityUpdate | None = None


@dataclass(frozen=True)
class Activity:
    item_id: str | None
    kind: ActivityKind
    label: str
    state: ActivityState
    content: dict[str, str] = field(default_factory=dict)

    @property
    def text(self) -> str:
        if self.kind == "reasoning":
            # Prefer the provider's public summary when both channels are present.
            prefix = "summary:" if any(k.startswith("summary:") and v for k, v in self.content.items()) else "content:"
            parts = sorted((k, v) for k, v in self.content.items() if k.startswith(prefix))
            return "\n\n".join(value for _, value in parts if value)
        return "\n".join(value for value in self.content.values() if value)


def finish_activities(activities: list[Activity], state: ActivityState = "complete") -> list[Activity]:
    return [replace(item, state=state) if item.state == "running" else item for item in activities]


def reduce_progress(
    activities: list[Activity], progress: AgentProgress, *, max_chars: int = 100_000, max_items: int = 200,
) -> list[Activity]:
    """Apply one update without mutating earlier snapshots or duplicating items."""
    update = progress.activity or ActivityUpdate(None, "log", "Activity", "complete", {"text": progress.text})
    if update.kind == "lifecycle":
        return finish_activities(activities, update.state) if update.state != "running" else list(activities)
    result = list(activities)
    if update.kind == "error":
        result = finish_activities(result, "error")
    index = next((i for i, item in enumerate(result) if update.item_id is not None and item.item_id == update.item_id), None)
    old = result[index] if index is not None else None
    if old is not None and old.state != "running" and update.append:
        return result  # Late deltas must not duplicate a completed snapshot.
    content = dict(old.content) if old else {}
    for section, text in update.content.items():
        if text:
            content[section] = content.get(section, "") + text if update.append else text
    state = old.state if old and old.state != "running" and update.state == "running" else update.state
    label = old.label if old and update.append else update.label
    item = Activity(update.item_id, update.kind, label, state, content)
    if index is None:
        result.append(item)
    else:
        result[index] = item

    # Bound even a single huge command output, retaining the newest information.
    remaining = max(0, max_chars)
    bounded = []
    for item in reversed(result[-max_items:] if max_items > 0 else []):
        if remaining <= 0:
            break
        content = {}
        for key, value in reversed(list(item.content.items())):
            text = value[-remaining:] if remaining else ""
            content[key] = text
            remaining -= len(text)
        bounded.append(replace(item, content=dict(reversed(list(content.items())))))
    return list(reversed(bounded))
