"""Streamlit views for provider-independent activity snapshots and chat turns."""
from __future__ import annotations

from contextlib import nullcontext
from itertools import groupby
from collections.abc import Sequence
from typing import Any

import streamlit as st

from lightacademia.agent_progress import Activity


def render_agent_activities(
    activities: Sequence[Activity], *, final_response: str | None = None, collapsed: bool = False,
) -> None:
    items = list(activities)
    # The final answer is rendered by the transcript, once, outside the activity UI.
    if final_response:
        last_message = next((i for i in range(len(items) - 1, -1, -1) if items[i].kind == "message"), None)
        if last_message is not None:
            text = items[last_message].text.strip()
            # Bounded activity may retain only the tail of a long final reply.
            if text and final_response.strip().endswith(text):
                items.pop(last_message)
    for is_message, group in groupby(items, key=lambda item: item.kind == "message"):
        entries = list(group)
        if is_message:
            for item in entries:
                if item.text:
                    with st.chat_message("assistant"):
                        st.markdown(item.text)
            continue
        with st.chat_message("assistant", avatar=":material/settings:"):
            container = st.expander(f"Activity · {len(entries)} steps", expanded=False, type="compact") if collapsed else nullcontext()
            with container:
                for item in entries:
                    label = item.label
                    if item.kind == "reasoning":
                        label = "Thinking" if item.state == "running" else "Thinking interrupted" if item.state == "error" else "Thought"
                    status = st.status(label, state=item.state, expanded=item.state == "error", type="step")
                    if item.text:
                        if item.kind in {"command", "files"}:
                            status.code(item.text, language="text")
                        else:
                            status.markdown(item.text)


def render_agent_transcript(messages: Sequence[dict[str, Any]]) -> None:
    for message in messages:
        activities = message.get("activities", [])
        if activities:
            render_agent_activities(activities, final_response=message["content"], collapsed=True)
        if message["content"]:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])
