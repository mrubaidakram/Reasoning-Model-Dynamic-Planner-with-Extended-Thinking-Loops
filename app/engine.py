"""Bounded plan/execute/recover workflow backed by LangGraph checkpoints."""

from __future__ import annotations

import ast
import json
import operator
import os
from typing import Annotated, Literal

import httpx
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, Field


MAX_STEPS = max(1, min(int(os.getenv("MAX_STEPS", "8")), 20))
MAX_RETRIES = max(0, min(int(os.getenv("MAX_RETRIES_PER_STEP", "2")), 5))


class PlanStep(BaseModel):
    id: str = Field(min_length=1, max_length=40)
    description: str = Field(min_length=1, max_length=500)
    tool: Literal["calculate", "summarize", "manual_review"]
    input: str = Field(min_length=1, max_length=4000)


class Plan(BaseModel):
    steps: list[PlanStep] = Field(min_length=1, max_length=MAX_STEPS)
    final_response: str = Field(min_length=1, max_length=1000)


def add_events(left: list[dict[str, str]], right: list[dict[str, str]]) -> list[dict[str, str]]:
    return left + right


class GraphState(TypedDict, total=False):
    task: str
    plan: list[dict[str, str]]
    final_response: str
    cursor: int
    retries: int
    events: Annotated[list[dict[str, str]], add_events]
    current_output: str
    current_error: str
    pending_approval: dict[str, str]
    approved: bool
    status: str


def model_json(system: str, user: str) -> str:
    base_url = os.getenv("MODEL_BASE_URL", "").rstrip("/")
    api_key = os.getenv("MODEL_API_KEY", "")
    model = os.getenv("MODEL_NAME", "")
    if not all((base_url, api_key, model)):
        raise RuntimeError("Configure MODEL_BASE_URL, MODEL_API_KEY, and MODEL_NAME")
    response = httpx.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
        },
        timeout=45,
    )
    response.raise_for_status()
    payload = response.json()
    content = payload["choices"][0]["message"]["content"]
    if not isinstance(content, str):
        raise ValueError("Model response content was not text")
    return content


def plan_node(state: GraphState) -> dict[str, object]:
    raw = model_json(
        "Return JSON with steps array and final_response. Each step must have id, description, tool, input. "
        "Allowed tools: calculate (arithmetic expression), summarize (text to summarize), manual_review (approval request). "
        "Do not include private reasoning or hidden chain of thought. Make a short executable plan.",
        state["task"],
    )
    plan = Plan.model_validate_json(raw)
    events = [{"type": "plan", "message": f"{step.description} [{step.tool}]"} for step in plan.steps]
    return {
        "plan": [step.model_dump() for step in plan.steps],
        "final_response": plan.final_response,
        "cursor": 0,
        "retries": 0,
        "events": events,
        "status": "running",
    }


ALLOWED_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
}
ALLOWED_UNARY_OPERATORS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def safe_calculate(expression: str) -> float:
    if len(expression) > 200:
        raise ValueError("Expression is too long")
    tree = ast.parse(expression, mode="eval")

    def evaluate(node: ast.AST) -> float:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return float(node.value)
        if isinstance(node, ast.BinOp) and type(node.op) in ALLOWED_BINARY_OPERATORS:
            left, right = evaluate(node.left), evaluate(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 8:
                raise ValueError("Exponent is out of bounds")
            return float(ALLOWED_BINARY_OPERATORS[type(node.op)](left, right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in ALLOWED_UNARY_OPERATORS:
            return float(ALLOWED_UNARY_OPERATORS[type(node.op)](evaluate(node.operand)))
        raise ValueError("Only numeric arithmetic operators are allowed")

    result = evaluate(tree.body)
    if abs(result) > 1e100:
        raise ValueError("Calculation result is out of bounds")
    return result


def execute_node(state: GraphState) -> dict[str, object]:
    step = state["plan"][state["cursor"]]
    if step["tool"] == "manual_review":
        approved = interrupt({"approval_required": True, "description": step["description"], "request": step["input"]})
        if not isinstance(approved, dict) or approved.get("approved") is not True:
            return {
                "current_error": "Human approval was not granted",
                "events": [{"type": "step_error", "message": f"{step['id']} was not approved"}],
            }
        output = str(approved.get("note", "Approved by reviewer"))[:500]
        return {
            "current_output": output,
            "current_error": "",
            "pending_approval": {},
            "approved": True,
            "retries": 0,
            "events": [{"type": "tool_result", "message": f"{step['id']}: {output}"}],
        }
    try:
        if step["tool"] == "calculate":
            output = str(safe_calculate(step["input"]))
        elif step["tool"] == "summarize":
            output = model_json(
                "Summarize the supplied text in a concise sentence. Return a JSON object with a string field named summary.",
                step["input"],
            )
            parsed = json.loads(output)
            output = parsed["summary"]
            if not isinstance(output, str):
                raise ValueError("Summary response was not text")
        else:
            raise ValueError("Unsupported tool")
        return {
            "current_output": output,
            "current_error": "",
            "pending_approval": {},
            "approved": True,
            "retries": 0,
            "events": [{"type": "tool_result", "message": f"{step['id']}: {output[:500]}"}],
        }
    except Exception as exc:
        return {
            "current_error": f"{type(exc).__name__}: {str(exc)[:300]}",
            "events": [{"type": "step_error", "message": f"{step['id']} failed: {type(exc).__name__}"}],
        }


def recover_node(state: GraphState) -> dict[str, object]:
    # Retry is bounded and only repeats a schema-validated, restricted step.
    next_retry = state.get("retries", 0) + 1
    if next_retry <= MAX_RETRIES:
        return {
            "retries": next_retry,
            "events": [{"type": "retry", "message": f"Retry {next_retry}/{MAX_RETRIES} scheduled"}],
        }
    return {"status": "failed", "events": [{"type": "failed", "message": "Step failed after bounded retries"}]}


def advance_node(state: GraphState) -> dict[str, object]:
    next_cursor = state["cursor"] + 1
    if next_cursor >= len(state["plan"]):
        outputs = [event["message"] for event in state.get("events", []) if event.get("type") == "tool_result"]
        summary = "\n".join(f"- {output}" for output in outputs) or "No tool output was produced."
        return {
            "cursor": next_cursor,
            "status": "completed",
            "final_response": f"Task execution completed. Results:\n{summary}",
        }
    return {"cursor": next_cursor, "status": "running"}


def route_after_execute(state: GraphState) -> str:
    if state.get("current_error"):
        return "recover"
    if state.get("cursor", 0) + 1 >= len(state["plan"]):
        return "advance"
    return "advance"


def route_after_advance(state: GraphState) -> str:
    if state.get("status") == "completed":
        return "finish"
    return "execute"


def route_after_recover(state: GraphState) -> str:
    return "execute" if state.get("status") != "failed" else "finish"


def build_graph(checkpointer: PostgresSaver):
    workflow = StateGraph(GraphState)
    workflow.add_node("plan", plan_node)
    workflow.add_node("execute", execute_node)
    workflow.add_node("recover", recover_node)
    workflow.add_node("advance", advance_node)
    workflow.add_edge(START, "plan")
    workflow.add_edge("plan", "execute")
    workflow.add_conditional_edges("execute", route_after_execute, {"recover": "recover", "advance": "advance"})
    workflow.add_conditional_edges("recover", route_after_recover, {"execute": "execute", "finish": END})
    workflow.add_conditional_edges("advance", route_after_advance, {"execute": "execute", "finish": END})
    return workflow.compile(checkpointer=checkpointer)


