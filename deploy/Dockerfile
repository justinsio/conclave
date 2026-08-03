# Conclave API image.
#
# Built by both the `api` and `migrate` compose services — same image, different
# command. migrate runs scripts/apply_migrations.py to completion and exits;
# api waits on that exit before starting. That ordering is not a nicety: the
# migration runner records applied filenames and skips them permanently, so a
# data migration runs exactly ONCE, ever. Starting the app first leaves rows
# wrong forever, because the migration will not run again to correct them.

FROM python:3.12-slim

# Non-root. The app never writes to its own directory.
RUN useradd -m -u 10001 conclave

WORKDIR /app

# Dependencies first, so editing code does not invalidate the install layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY migrations/ ./migrations/
COPY scripts/ ./scripts/

USER conclave

EXPOSE 8000

# --workers 1 is MANDATORY and must never be raised.
#
# The application lifespan starts NINE in-process background workers — post
# expiry sweeps, moderation timeouts, cost accounting, and others. A second
# uvicorn worker double-runs every one of them. This container therefore cannot
# be scaled horizontally: do not add `deploy.replicas`, and do not "tune" this.
#
# Binding 0.0.0.0 *inside* the container is correct. Compose publishes the port
# to 127.0.0.1 on the host, which is where the systemd unit's --host 127.0.0.1
# protection actually lives.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
