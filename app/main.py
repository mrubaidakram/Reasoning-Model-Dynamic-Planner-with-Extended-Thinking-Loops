"""HTTP API for creating, observing, and resuming bounded planner runs."""

from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from langgraph.checkpoint.postgres import PostgresSaver
from pydantic import BaseModel, Field

from app.engine import build_graph, run_graph


THREAD_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


class RunRequest(BaseModel):
    task: str = Field(min_length=1, max_length=8000)


class ApprovalRequest(BaseModel):
    approved: bool
    note: str = Field(default="", max_length=500)


@asynccontextmanager
async def lifespan(app: FastAPI):
    import os

    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        raise RuntimeError("DATABASE_URL must be configured")
    saver_context = PostgresSaver.from_conn_string(database_url)
    saver = saver_context.__enter__()
    saver.setup()
    app.state.saver_context = saver_context
    app.state.graph = build_graph(saver)
    try:
        yield
    finally:
        saver_context.__exit__(None, None, None)


app = FastAPI(title="Reasoning Model Dynamic Planner", version="1.0.0", lifespan=lifespan)


def validate_thread_id(thread_id: str) -> str:
    if not THREAD_ID_PATTERN.fullmatch(thread_id):
        raise HTTPException(status_code=400, detail="thread_id must contain 1-80 letters, digits, underscores, or hyphens")
    return thread_id


def public_state(values: dict[str, Any], next_nodes: tuple[str, ...]) -> dict[str, Any]:
    status = values.get("status", "running")
    if next_nodes and "execute" in next_nodes and values.get("plan"):
        current = values["plan"][values.get("cursor", 0)]
        if current.get("tool") == "manual_review":
            status = "awaiting_approval"
            pending = {"description": current["description"], "request": current["input"]}
        else:
            pending = None
    else:
        pending = None
    return {
        "status": status,
        "plan": values.get("plan", []),
        "events": values.get("events", []),
        "final_response": values.get("final_response", ""),
        "pending_approval": pending,
    }


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/runs", status_code=202)
async def create_run(request: RunRequest) -> dict[str, str]:
    thread_id = str(uuid4())
    try:
        await asyncio.to_thread(app.state.graph.invoke, {"task": request.task, "events": []}, {"configurable": {"thread_id": thread_id}})
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Planner run failed: {type(exc).__name__}") from exc
    return {"thread_id": thread_id, "status_url": f"/runs/{thread_id}"}


@app.get("/runs/{thread_id}")
async def get_run(thread_id: str) -> dict[str, Any]:
    thread_id = validate_thread_id(thread_id)
    snapshot = await asyncio.to_thread(app.state.graph.get_state, {"configurable": {"thread_id": thread_id}})
    if not snapshot.values:
        raise HTTPException(status_code=404, detail="Run not found")
    return public_state(snapshot.values, snapshot.next)


@app.post("/runs/{thread_id}/approval")
async def resume_run(thread_id: str, approval: ApprovalRequest) -> dict[str, Any]:
    thread_id = validate_thread_id(thread_id)
    try:
        result = await asyncio.to_thread(
            app.state.graph.invoke,
            __import__("langgraph.types", fromlist=["Command"]).Command(resume=approval.model_dump()),
            {"configurable": {"thread_id": thread_id}},
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Planner resume failed: {type(exc).__name__}") from exc
    return public_state(result, ())

