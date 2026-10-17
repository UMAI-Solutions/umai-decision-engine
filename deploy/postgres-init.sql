-- Runs once when the Postgres data directory is created (docker-entrypoint-initdb.d) or by an
-- operator on a managed server (ADR-0025 §1, §3). The application role is not the table owner,
-- has no BYPASSRLS, is not a superuser and must not be a member of azure_pg_admin.
-- Password: the UMAI_DB_APP_PASSWORD environment variable, substituted by the init wrapper.
CREATE ROLE umai_app LOGIN PASSWORD :'app_password' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
GRANT CONNECT ON DATABASE umai TO umai_app;
GRANT USAGE ON SCHEMA public TO umai_app;
