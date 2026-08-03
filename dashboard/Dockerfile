# Conclave operator dashboard (Streamlit).
#
# Optional — started only with `--profile dashboard`.

FROM python:3.12-slim

RUN useradd -m -u 10002 dashboard

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

USER dashboard

EXPOSE 8503

# --server.address=0.0.0.0 overrides .streamlit/config.toml, which sets
# 127.0.0.1 for the SSH-tunnel deployment. Inside a container that binds
# container-loopback, which a published port cannot reach — the container would
# start and then be silently unreachable.
#
# This does NOT make the dashboard public: it has no authentication of its own,
# and compose publishes it to host loopback only. But it IS reachable
# unauthenticated from any container on the same compose network, including the
# seeds. See DEPLOY.md.
CMD ["streamlit", "run", "Home.py", "--server.address=0.0.0.0", "--server.port=8503"]
