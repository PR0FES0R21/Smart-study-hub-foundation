"""Independent backup/restore CLI. PostgreSQL tools + restic; Python standard library only."""

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit
from urllib.request import Request, urlopen


class OperationError(Exception):
    pass


def now():
    return datetime.now(UTC).isoformat()


def event(name, **fields):
    print(json.dumps({"time": now(), "event": name, **fields}), flush=True)


def run(args, *, env=None, timeout=3600):
    result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        # pg/restic stderr may include connection credentials or private object names.
        raise OperationError(f"{Path(args[0]).name}_failed_{result.returncode}")
    return result.stdout


def database_env(secret_path):
    raw = Path(secret_path).read_text().strip()
    url = urlsplit(raw)
    if (
        url.scheme not in {"postgresql", "postgresql+asyncpg", "postgresql+psycopg"}
        or not url.hostname
        or not url.path.strip("/")
    ):
        raise OperationError("invalid_database_secret")
    env = dict(os.environ)
    env.update(
        PGHOST=url.hostname,
        PGPORT=str(url.port or 5432),
        PGDATABASE=unquote(url.path.lstrip("/")),
        PGUSER=unquote(url.username or ""),
        PGPASSWORD=unquote(url.password or ""),
        PGCONNECT_TIMEOUT="10",
    )
    query = parse_qs(url.query)
    env["PGSSLMODE"] = query.get("sslmode", ["verify-full"])[0]
    if env["PGSSLMODE"] not in {"disable", "require", "verify-ca", "verify-full"}:
        raise OperationError("unsupported_ssl_mode")
    return env


class Backup:
    def __init__(self):
        os.umask(0o077)
        self.state = Path(os.environ.get("STUDYHUB_BACKUP_STATE_DIR", "/backup/state"))
        self.state.mkdir(parents=True, exist_ok=True)
        self.tag = os.environ.get("STUDYHUB_BACKUP_TAG", "studyhub-production")
        self.host = os.environ.get("STUDYHUB_BACKUP_HOST", "studyhub-vps-1")
        self.restic = os.environ.get("RESTIC_BIN", "restic")
        self.dump = os.environ.get("PG_DUMP_BIN", "pg_dump")
        self.restore = os.environ.get("PG_RESTORE_BIN", "pg_restore")
        self.psql = os.environ.get("PSQL_BIN", "psql")
        self.db_secret = os.environ.get(
            "STUDYHUB_BACKUP_DATABASE_URL_FILE", "/run/secrets/backup_database_url"
        )
        self.status_file = self.state / "status.json"
        self.base = [self.restic]

    @contextmanager
    def lock(self):
        with (self.state / "operation.lock").open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise OperationError("another_backup_operation_is_running") from exc
            yield

    def status(self, **fields):
        try:
            previous = json.loads(self.status_file.read_text())
        except (OSError, ValueError):
            previous = {}
        temporary = self.status_file.with_suffix(".tmp")
        temporary.write_text(json.dumps({**previous, **fields}, indent=2))
        temporary.replace(self.status_file)

    def notify_failure(self, operation):
        secret = os.environ.get("STUDYHUB_OPS_WEBHOOK_URL_FILE")
        if not secret:
            return
        try:
            url = Path(secret).read_text().strip()
            if urlsplit(url).scheme != "https":
                raise OperationError("webhook_requires_https")
            payload = json.dumps(
                {
                    "text": f"StudyHub {operation} FAILED on {self.host}; inspect private operations logs."
                }
            ).encode()
            with urlopen(
                Request(url, data=payload, headers={"Content-Type": "application/json"}), timeout=10
            ):
                pass
        except Exception:
            event("ops_notification_failed")

    def validate_repository(self):
        for name in ("RESTIC_REPOSITORY_FILE", "RESTIC_PASSWORD_FILE"):
            if not os.environ.get(name) or not Path(os.environ[name]).read_text().strip():
                raise OperationError(f"{name.lower()}_required")

    def backup(self):
        env = database_env(self.db_secret)
        privileged = run(
            [
                self.psql,
                "-X",
                "-A",
                "-t",
                "-c",
                "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user",
            ],
            env=env,
            timeout=30,
        ).strip()
        if privileged != "t":
            raise OperationError("backup_role_must_cover_all_rls_tenants")
        staging = self.state / "staging"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(mode=0o700)
        started = time.monotonic()
        try:
            dump = staging / "database.dump"
            run(
                [self.dump, "--format=custom", "--no-owner", "--no-acl", "--file", str(dump)],
                env=env,
            )
            run([self.restore, "--list", str(dump)], timeout=60)
            with dump.open("rb") as handle:
                checksum = hashlib.file_digest(handle, "sha256").hexdigest()
            manifest = {
                "created_at": now(),
                "sha256": checksum,
                "bytes": dump.stat().st_size,
                "format": "pg_dump-custom",
                "host": self.host,
                "tag": self.tag,
            }
            uploads = os.environ.get("STUDYHUB_BACKUP_UPLOADS_DIR")
            if uploads and not Path(uploads).is_dir():
                raise OperationError("uploads_directory_missing")
            manifest["uploads_included"] = bool(uploads)
            (staging / "manifest.json").write_text(json.dumps(manifest))
            args = self.base + [
                "backup",
                "--host",
                self.host,
                "--tag",
                self.tag,
                "--json",
                str(staging),
            ]
            if uploads:
                args.append(uploads)
            output = run(args)
            summaries = [
                row
                for line in output.splitlines()
                if (row := json.loads(line)).get("message_type") == "summary"
            ]
            if not summaries or not summaries[-1].get("snapshot_id"):
                raise OperationError("snapshot_not_confirmed")
            snapshot = summaries[-1]["snapshot_id"]
            self.status(last_backup_success_at=now(), snapshot_id=snapshot, last_error=None)
            event(
                "backup_completed",
                snapshot_id=snapshot,
                duration_seconds=round(time.monotonic() - started, 2),
            )
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def retention(self):
        run(
            self.base
            + [
                "forget",
                "--host",
                self.host,
                "--tag",
                self.tag,
                "--group-by",
                "host,tags",
                "--keep-daily",
                "7",
                "--keep-weekly",
                "4",
                "--keep-monthly",
                "6",
            ]
        )
        event("retention_completed")

    def verify(self, snapshot, target_secret):
        target_env = database_env(target_secret)
        source_env = database_env(self.db_secret)

        def identity(env):
            return tuple(env[k] for k in ("PGHOST", "PGPORT", "PGDATABASE"))

        if identity(target_env) == identity(source_env) or not target_env["PGDATABASE"].endswith(
            "_restore_test"
        ):
            raise OperationError("restore_requires_separate_database_ending_restore_test")
        # Never --clean, --create, or drop objects on an existing target.
        count = run(
            [
                self.psql,
                "-X",
                "-A",
                "-t",
                "-c",
                "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%' AND c.relkind IN ('r','p','v','m','S')",
            ],
            env=target_env,
            timeout=30,
        ).strip()
        if count != "0":
            raise OperationError("restore_target_must_be_empty")
        with tempfile.TemporaryDirectory(prefix="restore-", dir=self.state) as directory:
            run(
                self.base
                + [
                    "restore",
                    snapshot,
                    "--host",
                    self.host,
                    "--tag",
                    self.tag,
                    "--target",
                    directory,
                ]
            )
            source_state = Path(
                os.environ.get("STUDYHUB_RESTORE_SOURCE_STATE_DIR", str(self.state.resolve()))
            )
            if not source_state.is_absolute() or ".." in source_state.parts:
                raise OperationError("invalid_snapshot_state_path")
            staging = Path(directory) / source_state.relative_to(source_state.anchor) / "staging"
            manifest_path = staging / "manifest.json"
            dump = staging / "database.dump"
            if not manifest_path.is_file() or not dump.is_file():
                raise OperationError("database_snapshot_files_missing")
            manifest = json.loads(manifest_path.read_text())
            with dump.open("rb") as handle:
                if hashlib.file_digest(handle, "sha256").hexdigest() != manifest["sha256"]:
                    raise OperationError("dump_checksum_mismatch")
            run(
                [
                    self.restore,
                    "--exit-on-error",
                    "--single-transaction",
                    "--no-owner",
                    "--no-acl",
                    "--dbname",
                    target_env["PGDATABASE"],
                    str(dump),
                ],
                env=target_env,
            )
            sql = "SELECT json_build_object('schools',(SELECT count(*) FROM schools),'users',(SELECT count(*) FROM users),'subjects',(SELECT count(*) FROM subjects),'migration',(SELECT version_num FROM alembic_version LIMIT 1))"
            restored = json.loads(
                run([self.psql, "-X", "-A", "-t", "-c", sql], env=target_env, timeout=30)
            )
            if not restored.get("migration"):
                raise OperationError("restored_schema_not_valid")
            self.status(last_restore_verified_at=now(), last_error=None)
            event("restore_verified", tables_checked=list(restored))

    def execute(self, operation, **kwargs):
        with self.lock():
            self.status(last_attempt_at=now(), last_operation=operation)
            try:
                self.validate_repository()
                if operation == "init":
                    run(self.base + ["init"])
                    event("repository_initialized")
                elif operation == "backup":
                    self.backup()
                    self.retention()
                elif operation == "maintenance":
                    run(self.base + ["check", "--read-data-subset=10%"])
                    run(self.base + ["prune"])
                    self.status(last_repository_check_at=now(), last_error=None)
                    event("repository_maintenance_completed")
                elif operation == "verify":
                    self.verify(kwargs["snapshot"], kwargs["target_secret"])
            except Exception as exc:
                code = str(exc) if isinstance(exc, OperationError) else type(exc).__name__
                self.status(last_error=code, last_failure_at=now())
                event(
                    "backup_operation_failed",
                    operation=operation,
                    error_type=type(exc).__name__,
                    error_code=code,
                )
                self.notify_failure(operation)
                raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["init", "backup", "maintenance", "verify"])
    parser.add_argument("--snapshot", default="latest")
    parser.add_argument("--target-secret")
    args = parser.parse_args()
    if args.operation == "verify" and not args.target_secret:
        parser.error("verify requires --target-secret for a separate empty database")
    try:
        Backup().execute(args.operation, snapshot=args.snapshot, target_secret=args.target_secret)
    except Exception as exc:
        event("operation_aborted", error_type=type(exc).__name__)
        sys.exit(1)


if __name__ == "__main__":
    main()
