# Smart Study Hub — Foundation

Mulai dari [peta dokumentasi dan status penerapan](docs/README.md).
Untuk pemisahan host aplikasi, Redis, dan PostgreSQL, baca
[panduan deployment tiga VPS](docs/DEPLOYMENT_TIGA_VPS.md).

Fondasi backend baru untuk pengembangan Smart Study Hub. FastAPI + PostgreSQL 16,
SQLAlchemy 2 async, Alembic, Celery dan Redis. Ini proyek terpisah; tidak menyalin
fitur utuh atau memigrasikan data MongoDB proyek lama.

Rilis **0.6.0** melengkapi CRUD siswa, guru, admin sekolah, kelas, program keahlian,
tahun ajaran dan semester. Biodata dipisah dari identitas login global; penghapusan
akses mempertahankan riwayat dan wali kelas disimpan per semester. Baca
[panduan CRUD, struktur data dan migrasi](docs/CRUD_DAN_STRUKTUR_DATA.md).

Rilis **0.5.0** menambahkan riwayat siswa per semester, kenaikan/tinggal kelas,
kelulusan SMP/SMA/SMK, roster ujian, autosave jawaban, deadline server dan Celery,
penilaian pilihan ganda/esai serta rilis nilai. Baca
[panduan siswa dan pelaksanaan ujian](docs/SISWA_DAN_PELAKSANAAN_UJIAN.md).

Rilis **0.4.0** menambahkan periode akademik, penugasan mengajar, koordinator
mapel per tingkat kelas, pengajuan/review soal, modul ujian berversi dan konfigurasi
ujian kelas dari paket bersama. Baca [panduan kolaborasi dan contoh API](docs/KOLABORASI_UJIAN.md).

Rilis **0.3.1** menerapkan perbaikan audit autentikasi, enrollment, cache,
WebSocket, admission job, respons upstream, dan backup/watchdog. Baca
[catatan perbaikan dan urutan upgrade](docs/SECURITY_FIXES_0_3_1.md): migrasi
mencabut sesi lama akun tanpa password lokal, dan API/worker harus diperbarui bersama.

Versi 0.3 menambahkan backup terenkripsi/restore, jadwal systemd independen,
logging berkorelasi dan audit dasar, metrik/alert privat, cleanup operasional,
rotasi log, serta konfigurasi HTTPS produksi. Mulai belajar dari
[panduan teknis berbahasa Indonesia](docs/PANDUAN_TEKNIS.md), lalu ikuti
[backup/restore](docs/BACKUP_DAN_RESTORE.md) dan
[operasional VPS](docs/OPERASIONAL_VPS.md).
Timer, bucket backup dan notifikasi produksi memerlukan konfigurasi pada VPS;
menyimpan kode ini di GitHub tidak mengaktifkan layanan tersebut otomatis.

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
| `/api/v1/students`, `/teachers`, `/school-admins` | CRUD direktori; penonaktifan/pemulihan akses sekolah dan revisi untuk konflik edit |
| `/api/v1/academic-years`, `/academic-periods` | CRUD tahun/semester, kalender tanpa tumpang tindih, perlindungan riwayat |
| `/api/v1/classes`, `/expertise-programs`, `/class-offerings` | CRUD kelas/program, wali kelas dan ruangan per semester |
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
  academics/           # direktori, kalender/kelas, enrollment, tugas mengajar, koordinasi, audit
  assessments/         # soal, review, modul berversi dan konfigurasi ujian kelas
  core/                # konfigurasi, principal dan primitive keamanan
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

Suite integrasi backup memerlukan Restic 0.19.1 pada PATH (atau `RESTIC_BIN`)
dan akses Docker ke container PostgreSQL 16 untuk alat pg_dump/pg_restore/psql.
Set `STUDYHUB_TEST_POSTGRES_CONTAINER` jika nama container berbeda dari
`smart-study-foundation-postgres-1`. CI memasang Restic dengan checksum terverifikasi.
Tes backup memakai repository terenkripsi lokal dan memulihkan data ke database
tes baru; tidak mengakses bucket atau database produksi.

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
