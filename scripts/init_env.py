"""Create local-only credentials; never replace an existing environment file."""

import os
import secrets
from pathlib import Path

path = Path(".env")
database, cache, control, broker = [secrets.token_hex(24) for _ in range(4)]
application = secrets.token_hex(24)
content = f"""POSTGRES_PASSWORD={database}
APP_DATABASE_PASSWORD={application}
CACHE_PASSWORD={cache}
CONTROL_PASSWORD={control}
BROKER_PASSWORD={broker}
STUDYHUB_DATABASE_URL=postgresql+asyncpg://studyhub_app:{application}@localhost:55432/studyhub
STUDYHUB_MIGRATION_DATABASE_URL=postgresql+asyncpg://studyhub:{database}@localhost:55432/studyhub
STUDYHUB_CACHE_URL=redis://:{cache}@localhost:56379/0
STUDYHUB_CONTROL_URL=redis://:{control}@localhost:56380/0
STUDYHUB_BROKER_URL=redis://:{broker}@localhost:56381/0
STUDYHUB_JWT_SECRET={secrets.token_hex(48)}
STUDYHUB_NAMESPACE=studyhub:dev
STUDYHUB_ENVIRONMENT=development
STUDYHUB_ALLOWED_ORIGINS=["http://localhost:3000"]
STUDYHUB_ALLOWED_HOSTS=["localhost","127.0.0.1","testserver","api"]
"""
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as handle:
    handle.write(content)
print("Created .env with random local credentials (not displayed)")
