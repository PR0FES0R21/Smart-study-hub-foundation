FROM postgres:16-bookworm
COPY --from=restic/restic:0.19.1 /usr/bin/restic /usr/local/bin/restic
RUN apt-get update && apt-get install -y --no-install-recommends python3 ca-certificates && rm -rf /var/lib/apt/lists/*
COPY ops/backup/backup.py /ops/backup.py
ENTRYPOINT ["python3", "/ops/backup.py"]
