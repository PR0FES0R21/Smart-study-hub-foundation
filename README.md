# Smart Study Hub — Foundation

Fondasi backend baru untuk pengembangan Smart Study Hub. FastAPI + PostgreSQL 16,
SQLAlchemy 2 async, Alembic, Celery dan Redis. Ini proyek terpisah; tidak menyalin
fitur utuh atau memigrasikan data MongoDB proyek lama.

## Mulai dengan Docker

Memerlukan Python 3.12+ untuk generator konfigurasi dan Docker Compose v2.

```bash
python scripts/init_env.py
docker compose up --build -d
docker compose exec api python -m studyhub.cli create-admin \
  --email admin@example.org --name "School Administrator"
```

CLI membuat **platform admin**; sesudah login buat sekolah melalui `POST /api/v1/schools`.
Kirim `X-School-ID` pada endpoint akademik. Role admin/guru/siswa disimpan per sekolah.
Baca [multi-sekolah dan SSO](docs/multischool-sso.md) untuk alur lengkap.

Password admin diminta secara interaktif. Tidak ada akun/password admin bawaan.
Generator membuat `.env` berizin 0600 dengan kredensial lokal acak; jangan commit.
Migrasi dijalankan oleh service `migrate` sebelum API/worker dimulai.

- Swagger: <http://localhost:8000/docs>
- OpenAPI: <http://localhost:8000/api/v1/openapi.json>
- Liveness: `/health/live`
- Readiness: `/health/ready` memeriksa PostgreSQL, schema migrasi, broker,
  Redis control, subscriber, dan melaporkan cache yang boleh mengalami degradasi.

## Pengembangan lokal

```bash
python scripts/init_env.py  # hanya jika .env belum ada
uv sync --frozen
docker compose up -d postgres cache control broker
uv run alembic upgrade head
uv run python -m studyhub.provision
uv run python -m studyhub.cli create-admin --email admin@example.org --name "Admin"
uv run uvicorn studyhub.main:create_app --factory --reload --no-access-log --no-proxy-headers
```

Di dua terminal lain:

```bash
uv run celery -A studyhub.worker.celery_app:celery_app worker --loglevel=INFO --concurrency=2
uv run celery -A studyhub.worker.celery_app:celery_app beat --loglevel=INFO --schedule=/tmp/studyhub-beat
```

Jalankan tepat satu beat per environment. Beberapa dispatcher aman menggunakan
`FOR UPDATE SKIP LOCKED`; beat tunggal mengurangi pengiriman jadwal duplikat.

## Fitur contoh yang sudah berfungsi

| Endpoint | Akses / perilaku |
|---|---|
| `POST /api/v1/auth/login` | JSON email/password, access JWT + opaque refresh token |
| `POST /api/v1/auth/refresh` | Rotasi sekali pakai; replay mencabut session |
| `POST /api/v1/auth/logout` | Mencabut session dan access token terkait |
| `GET /api/v1/users/me` | Profil pengguna aktif |
| `GET, POST /api/v1/users` | Admin; membuat teacher/student, tidak menerima role admin |
| `GET /api/v1/subjects` | Pengguna aktif; pagination dibatasi |
| `GET /api/v1/subjects/{id}` | Detail dari PostgreSQL |
| `POST, PUT, DELETE /api/v1/subjects[/{id}]` | Admin; unique code, transaksi dan invalidasi |
| `GET /api/v1/subjects/statistics` | Contoh cache-aside dengan TTL 30 detik |
| `POST /api/v1/jobs/subject-report` | Admin/teacher; wajib header `Idempotency-Key` |
| `GET /api/v1/jobs/{id}` | Hanya pemilik job; hasil persisten di PostgreSQL |
| `POST /api/v1/auth/ws-ticket` | Membuat ticket sekali pakai, berlaku 30 detik |
| `WS /api/v1/ws?ticket=...` | WebSocket native; event privat lintas proses API |

Gunakan tombol **Authorize** Swagger untuk access token. Refresh token disimpan
aman oleh client. Untuk browser, simpan access token di memori; jangan menaruh
token jangka panjang di localStorage. Integrasi cookie memerlukan desain CSRF
dan endpoint tersendiri, tidak diaktifkan secara implisit di fondasi ini.

Alur job: login → buat ticket → buka WebSocket → kirim job dengan idempotency key
unik → terima `job.completed` → ambil hasil melalui HTTP. WebSocket bukan Socket.IO.
Balas pesan ping aplikasi dengan `{"type":"ping"}`; server menerima hanya pesan
tersebut. Dedup event memakai `event_id`. Setelah reconnect, ambil ulang status
job melalui HTTP karena Pub/Sub tidak menyimpan riwayat event.

SSO OIDC mendukung PKCE, state/nonce dan RS256/JWKS, dengan provisioning identitas
eksplisit. Aktifkan lewat konfigurasi IdP; pengujian memakai provider lokal. SMP,
SMA dan SMK memiliki aturan kelas/program yang divalidasi sampai database.

## Struktur kode

```text
src/studyhub/
  api/                 # route, validasi input/output, auth dependency, WebSocket
  core/                # konfigurasi dan primitive keamanan
  infrastructure/      # database factory, Redis, cache, Pub/Sub, outbound HTTP
  services/            # use case bisnis dan batas transaksi
  worker/              # Celery, resource sinkron per child, dispatcher outbox
  models.py            # model SQLAlchemy dan constraint PostgreSQL
  schemas.py           # DTO bersama; service tidak bergantung pada lapisan API
  main.py              # app factory dan lifespan resource
  cli.py               # provisioning admin eksplisit
migrations/            # revisi Alembic yang dapat upgrade/downgrade
tests/                 # unit dan integrasi dengan layanan nyata
docs/                  # keputusan arsitektur, keamanan, operasi, integrasi
```

Mulai fitur baru di `services/` dan `api/`, dengan model serta migrasi yang sesuai.
Untuk domain yang membesar (ujian, kelas, LMS), pecah `models.py` dan route menjadi
modul domain. Hindari base repository generik yang hanya membungkus SQLAlchemy.
Worker tidak mengimpor route/dependency API dan tidak memakai `asyncio.run()`.

## Pengujian

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest tests/test_unit.py -q
docker compose up -d postgres cache control broker
STUDYHUB_RUN_INTEGRATION=1 uv run pytest -q
```

Tes integrasi membuat database PostgreSQL baru dengan nama acak berakhiran `_test`,
menjalankan migrasi, dua proses API, Celery prefork worker dan beat. Database tes
dihapus sesudahnya. Akun fixture hanya ada di database tes. Gunakan PostgreSQL
development dengan izin `CREATE DATABASE`; jangan arahkan tes ke produksi.

Tes mencakup auth/replay/logout, RBAC, CRUD/unique constraint, invalidasi cache,
redaksi password pada validation error, isolasi pemilik job, idempotensi, ticket
sekali pakai, notifikasi worker ke proses API lain, cache fallback dan reconnect.
Ini tes fungsional; belum merupakan benchmark kapasitas atau audit keamanan penuh.

Baca [arsitektur](docs/architecture.md), [keamanan](docs/security.md),
[operasional](docs/operations.md), [integrasi](docs/integrations.md), serta
[multi-sekolah/SSO](docs/multischool-sso.md) sebelum deployment.
