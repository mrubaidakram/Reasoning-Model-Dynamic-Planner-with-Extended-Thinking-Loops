# Development guide

## Components

- `app/engine.py` defines plan schemas, safe arithmetic, model calls, and LangGraph workflow nodes.
- `app/main.py` serves the API and initializes PostgreSQL checkpoints.
- `app/ui.py` renders the Streamlit run dashboard.

## Configure a model provider

Set `MODEL_BASE_URL` to the provider's API root (the application appends `/chat/completions`), `MODEL_API_KEY` to a secret, and `MODEL_NAME` to a deployed model ID. The provider must support JSON object responses. Requests use a 45-second timeout and temperature 0. Provider responses are parsed and validated before execution; malformed plans fail the run rather than being executed.

## Change workflow steps

1. Add or update the Pydantic schema and allowed tool union.
2. Implement a deterministic dispatcher branch with bounded inputs/outputs.
3. Ensure side-effecting tools pause for explicit human review and have idempotency controls before adding them.
4. Update the Mermaid diagram and deployment/security notes.
5. Use the shared [Ubaid engineering standards](../../CODE_STANDARDS.md).

`MAX_STEPS` is clamped to 1–20. `MAX_RETRIES_PER_STEP` is clamped to 0–5. LangGraph checkpoint setup runs when the API starts; provision database permissions for its checkpoint tables.
