"""Independent minute-by-minute API/disk/backup watchdog; optional HTTPS webhook."""

import json
import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def check():
    failures = []
    url = os.environ.get("STUDYHUB_WATCH_URL", "http://127.0.0.1:8000/health/ready")
    parsed = urlsplit(url)
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
    ):
        failures.append("health_url_not_secure")
    else:
        try:
            with urlopen(url, timeout=5) as response:
                data = json.load(response)
                if data.get("status") != "ready":
                    failures.append("api_not_ready")
        except Exception:
            failures.append("api_unreachable")
    disk = shutil.disk_usage(os.environ.get("STUDYHUB_WATCH_DISK", "/"))
    if disk.free / disk.total < float(os.environ.get("STUDYHUB_WATCH_MIN_FREE_FRACTION", "0.10")):
        failures.append("disk_space_low")
    status = Path(
        os.environ.get("STUDYHUB_WATCH_BACKUP_STATUS", "/var/lib/studyhub-backup/status.json")
    )
    try:
        data = json.loads(status.read_text())
        age = time.time() - datetime.fromisoformat(data["last_backup_success_at"]).timestamp()
        if age > int(os.environ.get("STUDYHUB_WATCH_MAX_BACKUP_AGE_SECONDS", "93600")):
            failures.append("backup_stale")
        if data.get("last_error"):
            failures.append("backup_last_operation_failed")
    except (OSError, ValueError, KeyError):
        failures.append("backup_status_missing")
    return sorted(failures)


def main():
    os.umask(0o077)
    failures = check()
    state = Path(os.environ.get("STUDYHUB_WATCH_STATE_DIR", "/var/lib/studyhub-ops"))
    state.mkdir(parents=True, exist_ok=True)
    file = state / "watch.json"
    try:
        previous = json.loads(file.read_text())
    except (OSError, ValueError):
        previous = {}
    stamp = time.time()
    changed = failures != previous.get("failures", [])
    notify = changed or (failures and stamp - previous.get("notified_at", 0) >= 1800)
    notified = previous.get("notified_at", 0)
    secret = os.environ.get("STUDYHUB_OPS_WEBHOOK_URL_FILE")
    if notify and secret:
        try:
            url = Path(secret).read_text().strip()
            if urlsplit(url).scheme != "https":
                raise ValueError("HTTPS required")
            message = ", ".join(failures) if failures else "recovered"
            request = Request(
                url,
                data=json.dumps({"text": f"StudyHub operations: {message}"}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urlopen(request, timeout=10):
                pass
            notified = stamp
        except Exception:
            print(json.dumps({"event": "watch_notification_failed"}), flush=True)
    temporary = file.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"checked_at": stamp, "failures": failures, "notified_at": notified})
    )
    temporary.replace(file)
    print(
        json.dumps(
            {
                "event": "operations_watch",
                "failures": failures,
                "notification_configured": bool(secret),
            }
        ),
        flush=True,
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
