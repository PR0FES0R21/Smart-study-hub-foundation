"""Provision a non-superuser runtime role for the local development stack only."""

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from studyhub.core.config import get_settings


def main():
    settings = get_settings()
    if settings.environment == "production":
        raise SystemExit("Provision production roles using your privileged release process")
    runtime = make_url(settings.database_url.get_secret_value())
    migration = make_url(settings.sync_migration_url)
    if not settings.migration_database_url or runtime.username == migration.username:
        raise SystemExit("Configure separate runtime and migration credentials first")
    with psycopg.connect(
        migration.set(drivername="postgresql").render_as_string(hide_password=False)
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=%s", (runtime.username,)
            )
            existing = cursor.fetchone()
            if existing and any(existing):
                raise SystemExit("Refusing to reuse a privileged runtime role")
            operation = "ALTER ROLE" if existing else "CREATE ROLE"
            cursor.execute(
                sql.SQL(operation + " {} LOGIN PASSWORD {}").format(
                    sql.Identifier(runtime.username), sql.Literal(runtime.password)
                )
            )
            cursor.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(runtime.database), sql.Identifier(runtime.username)
                )
            )
            cursor.execute(
                sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(
                    sql.Identifier(runtime.username)
                )
            )
            cursor.execute(
                sql.SQL(
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}"
                ).format(sql.Identifier(runtime.username))
            )
    print("Development runtime role provisioned without SUPERUSER or BYPASSRLS")


if __name__ == "__main__":
    main()
