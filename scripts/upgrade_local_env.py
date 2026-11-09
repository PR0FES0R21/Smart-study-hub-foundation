"""One-time local v0.1 environment upgrade. Does not connect to any database."""

import os
import secrets
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy.engine import make_url

path = Path(".env")
values = dotenv_values(path)
if values.get("STUDYHUB_MIGRATION_DATABASE_URL"):
    print("Environment already has separate migration/runtime configuration")
else:
    original = values["STUDYHUB_DATABASE_URL"]
    password = secrets.token_hex(24)
    runtime = (
        make_url(original)
        .set(username="studyhub_app", password=password)
        .render_as_string(hide_password=False)
    )
    lines = [
        line
        for line in path.read_text().splitlines()
        if not line.startswith("STUDYHUB_DATABASE_URL=")
    ]
    lines += [
        f"APP_DATABASE_PASSWORD={password}",
        f"STUDYHUB_DATABASE_URL={runtime}",
        f"STUDYHUB_MIGRATION_DATABASE_URL={original}",
    ]
    path.write_text("\n".join(lines) + "\n")
    os.chmod(path, 0o600)
    print("Local environment upgraded; run Alembic upgrade and studyhub.provision next")
