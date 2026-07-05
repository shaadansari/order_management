#!/bin/bash
# WHY a wrapper script: in production we must run Alembic migrations BEFORE the app
# accepts traffic, so the schema is current the moment uvicorn starts. Doing this here
# (instead of inside the app) keeps migration failures visible in the container logs and
# stops the server from booting on a broken/missing schema (`set -e` aborts on failure).
set -e

if [ "$APP_ENV" = "production" ]; then
    echo "Running Alembic migrations..."
    alembic upgrade head
    echo "Migrations complete."
else
    echo "Non-production mode ($APP_ENV): skipping Alembic — create_all handles schema."
fi

echo "Starting uvicorn..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
