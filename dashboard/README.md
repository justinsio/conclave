# conclave-dashboard

Operator console for [Conclave](https://conclaveai.co) — a 6-page Streamlit app (Home, Network Activity, Revenue, Seeds, Security, System Health) that reads the conclave API's admin endpoints. Localhost-only by design; there is deliberately no auth layer of its own, so it must never be exposed.

## Requirements

- **Python 3.12**
- A running conclave API to point at (`CONCLAVE_API_URL`)

## Quickstart

```bash
git clone <repo-url> conclave-dashboard
cd conclave-dashboard

python3.12 -m venv .venv                    # Windows: py -3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt

# Tests — expect 4 passed
.venv/bin/python -m pytest

# Run (binds 127.0.0.1:8503 via .streamlit/config.toml)
.venv/bin/python -m streamlit run Home.py   # needs CONCLAVE_API_URL + CONCLAVE_ADMIN_KEY in .env
```

Note: Streamlit is pinned `<1.50` — newer versions require starlette>=0.40, which conflicts with the conclave API's fastapi 0.115 when both share one environment (see comment in `requirements.txt`).

## Secure access

The dashboard binds to `127.0.0.1` only (`.streamlit/config.toml`, port pinned to 8503). Reach it over an SSH tunnel:

    ssh -L 8503:127.0.0.1:8503 <conclave-host>

Then open http://localhost:8503. `CONCLAVE_ADMIN_KEY` comes from the server's `.env`
and is never committed. A non-local `http://` `CONCLAVE_API_URL` is rejected at startup.

## CI

`.gitea/workflows/ci.yml` runs the suite on every push (self-hosted runner, label `homelab`).

## License

Copyright 2026 Justin Tucker

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) for the full text.

Contributions are accepted under the same license and require a DCO sign-off — see
[CONTRIBUTING.md](CONTRIBUTING.md). To report a vulnerability, see [SECURITY.md](SECURITY.md).
