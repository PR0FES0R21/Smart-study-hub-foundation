"""Prepare local private operations files. Does not connect to remote storage or enable timers."""

import json
import os
import secrets
from pathlib import Path

from dotenv import dotenv_values, set_key

os.umask(0o077)
root = Path(__file__).resolve().parents[1]
private = root / ".secrets"
private.mkdir(mode=0o700, exist_ok=True)
private.chmod(0o700)
env = root / ".env"
if not env.exists():
    raise SystemExit("Run scripts/init_env.py first")
env.chmod(0o600)
values = dotenv_values(env)
hosts = json.loads(values.get("STUDYHUB_ALLOWED_HOSTS") or '["localhost","127.0.0.1"]')
if "api" not in hosts:
    hosts.append("api")
    set_key(
        env, "STUDYHUB_ALLOWED_HOSTS", json.dumps(hosts, separators=(",", ":")), quote_mode="never"
    )
token = values.get("STUDYHUB_METRICS_TOKEN") or secrets.token_urlsafe(48)
if not values.get("STUDYHUB_METRICS_TOKEN"):
    with env.open("a") as handle:
        handle.write(f"\nSTUDYHUB_METRICS_TOKEN={token}\n")
(private / "metrics_token").write_text(token)
# Readable in the isolated Prometheus container; host parent directory remains 0700.
(private / "metrics_token").chmod(0o444)
if not (private / "restic_password").exists():
    (private / "restic_password").write_text(secrets.token_urlsafe(48))
if not (root / ".env.backup").exists():
    (root / ".env.backup").write_text((root / "ops/backup/backup.env.example").read_text())
print("Operations secrets prepared locally; values are not displayed or committed.")
print(
    "Configure backup_database_url and restic_repository secret files before initializing backups."
)
print(
    "Keep an independent secure copy of restic_password; losing it makes encrypted backups unusable."
)
