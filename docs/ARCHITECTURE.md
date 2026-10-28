# Architecture and operating model

## State and persistence

Each run uses a unique LangGraph `thread_id`; the PostgreSQL checkpointer stores graph state so an approval interruption can be resumed. Treat checkpoint storage as sensitive because task text, bounded tool inputs, results, and event logs are persisted. Define retention and access controls before production use.

The UI receives a public projection of state: validated plan steps, result/error event summaries, status, and approval request. The model is explicitly instructed not to return hidden reasoning, and no chain-of-thought field is defined in the schema. Logs and client responses should continue to exclude provider credentials and full raw provider payloads.

## Failure behavior

- Invalid model JSON or schema: run fails; no invalid step is dispatched.
- Provider HTTP or JSON error: treated as a step failure and retried within the configured bound.
- Calculator input outside arithmetic grammar or numeric bounds: step fails safely.
- Rejected human approval: approval step fails; the bounded retry policy applies.
- Retry exhaustion: status is `failed`, with a concise event and no fabricated result.

## Security boundary

The starter API currently has no authentication or per-user authorization. Do not expose it to untrusted networks until an identity layer is installed and each thread is checked against its owner. Keep secrets in a secret manager, use TLS, restrict provider egress, and apply request/rate/concurrency limits. Do not add tools that can mutate external systems without explicit approval, idempotency, and audit controls.
