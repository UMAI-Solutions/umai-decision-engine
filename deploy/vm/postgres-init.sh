#!/bin/sh
# docker-entrypoint-initdb.d hook: creates the application role (no BYPASSRLS, not the table
# owner) from deploy/postgres-init.sql, with the password passed by umai.sh (ADR-0025 §1).
set -eu
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
	-v app_password="$UMAI_DB_APP_PASSWORD" -f /umai/postgres-init.sql
