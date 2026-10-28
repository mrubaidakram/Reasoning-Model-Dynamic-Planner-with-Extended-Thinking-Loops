# Deployment guide

1. Provision PostgreSQL and a dedicated database/user for LangGraph checkpoint tables.
2. Configure a supported OpenAI-compatible endpoint, API key, and model name via a secret manager.
3. Build from a reviewed, locked dependency set; the repository currently supplies compatible version ranges rather than a generated lock file.
4. Run the API behind TLS and an organization authentication/authorization proxy. Add run ownership checks before allowing multiple users.
5. Run Streamlit behind the same identity boundary and configure `API_URL` to the internal API address.
6. Monitor graph duration, model errors/latency, retries, approval wait time, and checkpoint database health. Avoid logging task content and provider credentials.
7. Set checkpoint retention and test backup/restore procedures before production use.

The API initializes LangGraph’s PostgreSQL checkpoint schema at startup. For a stricter migration process, run the supported checkpoint setup as a controlled deployment migration rather than granting schema creation privileges to the runtime role.
