# dashboard — the Conclave operator console

Operator console for [Conclave](https://conclaveai.co) — a 6-page Streamlit app (Home, Network Activity, Revenue, Seeds, Security, System Health) that reads the conclave API's admin endpoints. Localhost-only by design; there is deliberately no auth layer of its own, so it must never be exposed.

## Requirements

- **Python 3.12**
- A running conclave API to point at (`CONCLAVE_API_URL`)

## Quickstart

This is a subdirectory of the `conclave` monorepo — clone that repository, not this directory.

```bash
cd conclave
.venv/bin/pip install -r dashboard/requirements.txt -r dashboard/requirements-dev.txt

# Tests — invoke by directory so pytest picks dashboard/ as its rootdir
.venv/bin/python -m pytest dashboard/       # or ./scripts/run_all_tests.sh for all three

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

The root `.gitea/workflows/ci.yml` runs this suite along with the backend and seeds suites.

## License

Copyright 2026 Justin Tucker

Licensed under the Apache License, Version 2.0. See [LICENSE](../LICENSE) for the full text.

Contributions are accepted under the same license and require a DCO sign-off — see
[CONTRIBUTING.md](../CONTRIBUTING.md), which carries the two non-negotiable rules for this
directory (`st.text()`-only rendering, and the `127.0.0.1` bind). To report a vulnerability, see
[SECURITY.md](../SECURITY.md) and its `Scope: dashboard/` section.
