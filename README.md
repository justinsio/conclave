## Secure access

The dashboard binds to `127.0.0.1` only (`.streamlit/config.toml`). Reach it over an SSH tunnel:

    ssh -L 8503:127.0.0.1:8503 <conclave-host>

Then open http://localhost:8503. `CONCLAVE_ADMIN_KEY` comes from the server's `.env`
and is never committed. A non-local `http://` `CONCLAVE_API_URL` is rejected at startup.
