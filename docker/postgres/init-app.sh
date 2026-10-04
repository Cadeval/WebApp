#!/bin/sh
set -eu

# The application's role is distinct from the bootstrap administrator. Keep
# names URL-safe, bounded and outside PostgreSQL's reserved role/database names.
if ! printf '%s' "$CADEVIL_DB_USER" | grep -Eq '^[a-z_][a-z0-9_]{0,62}$' \
    || ! printf '%s' "$CADEVIL_DB_NAME" | grep -Eq '^[a-z_][a-z0-9_]{0,62}$'; then
    printf '%s\n' 'Application database names must be lowercase identifiers of at most 63 characters.' >&2
    exit 1
fi
case "$CADEVIL_DB_USER" in
    postgres|pg_*) printf '%s\n' 'The application must use a dedicated database role.' >&2; exit 1 ;;
esac
case "$CADEVIL_DB_NAME" in
    postgres|template0|template1|pg_*) printf '%s\n' 'The application must use a dedicated database.' >&2; exit 1 ;;
esac

# Read the application password inside the temporary bootstrap database.
# gexec executes the generated commands without printing credential-bearing
# result rows. Names alone are passed as psql arguments. Suppress query/error
# statement logging for this bootstrap session.
psql --username "$POSTGRES_USER" --dbname postgres --set ON_ERROR_STOP=1 \
    --set app_user="$CADEVIL_DB_USER" --set app_db="$CADEVIL_DB_NAME" <<'SQL'
SET log_statement = 'none';
SET log_min_error_statement = 'panic';
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
              :'app_user', rtrim(pg_read_file('/run/secrets/postgres_password'), E'\r\n'))
\gexec
SELECT format('CREATE DATABASE %I OWNER %I', :'app_db', :'app_user')
\gexec
SQL
