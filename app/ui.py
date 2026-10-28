"""Streamlit dashboard for the planner API."""

from __future__ import annotations

import os
import time
from typing import Any

import httpx
import streamlit as st


API_URL = os.getenv("API_URL", "http://127.0.0.1:8000").rstrip("/")
st.set_page_config(page_title="Dynamic Planner", page_icon="🧭", layout="wide")
st.title("Reasoning Model Dynamic Planner")
st.caption("Review the executable plan, tool outputs, and recovery events. Private model reasoning is not displayed.")

with st.form("new_run"):
    task = st.text_area("Task", placeholder="Describe a task for the planner", max_chars=8000)
    submitted = st.form_submit_button("Plan and run", type="primary")

if submitted:
    try:
        response = httpx.post(f"{API_URL}/runs", json={"task": task}, timeout=120)
        response.raise_for_status()
        st.session_state["thread_id"] = response.json()["thread_id"]
    except httpx.HTTPError as exc:
        st.error(f"Could not start run: {exc}")

thread_id = st.session_state.get("thread_id")
if thread_id:
    st.subheader("Run status")
    try:
        response = httpx.get(f"{API_URL}/runs/{thread_id}", timeout=15)
        response.raise_for_status()
        run: dict[str, Any] = response.json()
    except httpx.HTTPError as exc:
        st.error(f"Could not load run status: {exc}")
        run = {}

    if run:
        st.write(f"**Status:** {run['status']}")
        if run.get("plan"):
            st.subheader("Validated plan")
            for number, step in enumerate(run["plan"], start=1):
                st.markdown(f"**{number}. {step['description']}** — `{step['tool']}`")
        approval = run.get("pending_approval")
        if approval:
            st.warning(f"Approval required: {approval['description']}\n\n{approval['request']}")
            with st.form("approval"):
                note = st.text_input("Reviewer note", max_chars=500)
                approved = st.checkbox("Approve this step")
                decision = st.form_submit_button("Submit decision")
            if decision:
                try:
                    result = httpx.post(
                        f"{API_URL}/runs/{thread_id}/approval",
                        json={"approved": approved, "note": note},
                        timeout=120,
                    )
                    result.raise_for_status()
                    st.rerun()
                except httpx.HTTPError as exc:
                    st.error(f"Could not submit approval: {exc}")
        if run.get("events"):
            st.subheader("Execution trace")
            for event in run["events"]:
                st.markdown(f"- **{event['type']}**: {event['message']}")
        if run.get("final_response") and run["status"] == "completed":
            st.subheader("Model-proposed completion")
            st.write(run["final_response"])
        if run["status"] in {"running", "awaiting_approval"}:
            time.sleep(1)
            st.rerun()
