# Security & cost safety

- This tool sends **real** requests through your CC Switch proxy to upstream providers.
- Always set `--max-cost-usd` for large targets.
- Never commit API keys. Prefer environment variables or let CC Switch inject credentials.
- Do not expose the proxy beyond localhost.
- Forging usage by writing SQLite is intentionally unsupported (data integrity risk).
