FROM python:3.12-slim

WORKDIR /app

# Phase 4: a dedicated non-root user the process actually runs as (see
# entrypoint.py for why dropping to it happens there, not via a plain
# Dockerfile `USER app`). --system since this is a service account, not
# an interactive login; fixed uid so it's predictable across rebuilds.
RUN useradd --system --create-home --uid 1000 app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# main.py stays at the top level (the MCP entrypoint's path is
# unchanged); the real implementation now lives in the pmchaser/ package
# (see the Phase 1 structural refactor - the flat models.py/tools.py/
# telegram_client.py this used to copy no longer exist).
COPY main.py entrypoint.py ./
COPY pmchaser/ ./pmchaser/

# The SQLite file lives on a mounted volume (see docker-compose.yml) so
# task data survives a container restart/recreate.
ENV PM_CHASER_DB_PATH=/data/pm_chaser.db
ENV PM_CHASER_HOST=0.0.0.0
ENV PM_CHASER_PORT=8000

EXPOSE 8000

# Hits /healthz (main.py) rather than just checking the process is alive -
# a container that's up but can't reach its own SQLite file (bad volume
# mount, permissions) should report unhealthy. --start-period gives
# init_db()'s create_all()/index pass room to finish on first boot before
# failures start counting against the retry budget. Uses python, already
# in the image, rather than adding curl just for this.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request as u; u.urlopen('http://localhost:8000/healthz', timeout=3)" || exit 1

# Starts as root (no USER instruction) so entrypoint.py can chown /data
# and drop to the `app` user itself before exec'ing this. See
# entrypoint.py's module docstring for why that has to happen at
# container-start time rather than at build time.
ENTRYPOINT ["python", "entrypoint.py"]
CMD ["python", "main.py"]
