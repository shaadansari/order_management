FROM python:3.12-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM python:3.12-slim
WORKDIR /app

# Non-root user for security
# WHY: running as root in a container is a security risk — if the container
# is compromised, attacker gets root. Non-root limits the blast radius.
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY . .

RUN chown -R appuser:appgroup /app
USER appuser

EXPOSE 8000

# Health check — Render and Docker use this to know if the app is alive
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

# WHY start.sh as the command: it runs `alembic upgrade head` (in production) before
# starting uvicorn, so the schema is current before the app serves a request. The exec
# bit is forced here because a Windows git checkout may not carry Unix file modes into
# the image — `./start.sh` would fail to exec otherwise.
RUN chmod +x start.sh
CMD ["./start.sh"]
