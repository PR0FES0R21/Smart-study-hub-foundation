# Hasil verifikasi v0.3 — 10 November 2026

Python 3.12, PostgreSQL 16, tiga Redis 7.4, dua proses API, worker Celery prefork,
beat dan IdP lokal. Seluruh tes dijalankan dengan layanan nyata di lingkungan kerja.

| Pemeriksaan | Hasil |
|---|---|
| Ruff lint / format; `git diff --check` | Lulus |
| Suite unit + integrasi lengkap | **34 passed** |
| Migrasi schema termasuk index retensi, upgrade/check/downgrade/upgrade | Lulus dalam fixture integrasi |
| Restic 0.19.1 init → pg_dump → backup terenkripsi → check/prune | Lulus pada repository lokal sementara |
| Restore snapshot ke database PostgreSQL terpisah | Lulus; sekolah dan isi membership cocok dengan sumber |
| Restore ke sumber, nama target tidak aman, atau target berisi data | Ditolak |
| Lock backup untuk mencegah operasi bertumpuk | Lulus |
| Staging plaintext dibuang setelah backup selesai | Lulus |
| Logging: token/password/DSN/query credential dan raw exception | Disaring dalam tes; request ID login cocok dengan audit log |
| Error tak terduga | HTTP 500 umum + request ID; tercatat dalam metrik |
| Metrik privat, bearer salah/tidak ada, label tanpa ID sekolah | Lulus |
| Cleanup melalui worker Celery nyata | Session lama/outbox delivered lama terhapus; session aktif, token replay, pending outbox dipertahankan |
| Watchdog: API gagal dan backup tidak ada/basi | Terdeteksi |
| Gabungan Compose produksi+backup+monitoring | `docker compose config --quiet` lulus |
| Prometheus config syntax + 5 alert rules | Lulus dengan promtool v3.5.0 |
| Alertmanager config | Lulus dengan amtool v0.28.1 |
| Caddyfile | `caddy validate` lulus dengan Caddy v2.10.2 |
| Ekspresi jadwal harian systemd | `systemd-analyze calendar` lulus |

Binary alat verifikasi diunduh dari rilis resmi dan checksum distribusi diperiksa.
Tes restore menggunakan repository terenkripsi lokal, bukan bucket produksi.
Timer belum dipasang/diaktifkan, webhook/receiver eksternal belum dikonfigurasi,
dan DNS/TLS domain nyata belum dijalankan. Validasi unit systemd penuh tidak dapat
dijalankan karena lingkungan tidak mengizinkan penulisan runtime `/run/systemd`;
lakukan `systemd-analyze verify` serta smoke test timer pada host tujuan.

Image/seluruh stack Compose v0.3 belum dibangun ulang karena disk Docker VFS
terbatas. Tes proses API/worker dan backup/restore nyata lulus, tetapi ini tidak
menggantikan build image dan smoke test gabungan stack pada VPS tujuan.
Belum ada benchmark kapasitas, pengukuran RTO, HA/PITR, audit penetrasi, atau
verifikasi IdP/bucket/penerima alert produksi. CI juga harus diperiksa terpisah
di GitHub; hasil suite di atas berasal dari lingkungan kerja ini.

## Riwayat v0.2 — 9 November 2026

Python 3.12, PostgreSQL 16 dan tiga Redis 7.4 nyata; Celery tetap dipakai.

| Pemeriksaan | Hasil |
|---|---|
| Ruff lint / format | Lulus |
| Suite unit + integrasi | **26 passed** |
| Upgrade/check/downgrade/upgrade Alembic pada database kosong | Lulus |
| Backfill user, subject, job, outbox v0.1 ke tenant legacy | Lulus, data dipertahankan |
| Downgrade yang akan menghilangkan data multi-sekolah | Ditolak secara transaksional |
| Login, refresh rotation/replay, logout, RBAC, ownership, body limit | Lulus |
| User global dengan role membership berbeda di sekolah berbeda | Lulus |
| Subject, cache, job/idempotency dan hasil Celery per sekolah | Lulus |
| PostgreSQL RLS dengan role runtime non-superuser | Query tanpa konteks tidak melihat data; cross-school write ditolak |
| Context sekolah tidak bocor melalui pooled connection | Lulus |
| Program SMK dan aturan kelas SMP/SMA/SMK | Lulus validasi HTTP dan constraint SQL |
| Worker → Pub/Sub → WebSocket di API process lain | Lulus |
| Isolasi event user yang sama pada sekolah berbeda | Lulus unit |
| OIDC authorization code, PKCE S256, browser state binding, RS256/JWKS | Lulus dengan IdP lokal terpisah |
| OIDC replay, nonce/audience/issuer salah, token expired/unsigned | Ditolak |
| Auto-link SSO hanya berdasarkan email yang sama | Ditolak |
| Redaksi code/ticket dan password pada log/error | Lulus |
| Reconciliation job tanpa pesan broker | Lulus |
| Compose v0.2 config validation | Lulus |
| Migrasi/provisioning database development aktual | Lulus; runtime SUPERUSER=false, BYPASSRLS=false |
| API v0.2 development readiness/OpenAPI smoke test | HTTP 200 |

Suite memakai database acak berakhiran `_test`, role runtime tanpa SUPERUSER dan
BYPASSRLS, dua proses API, worker Celery prefork, beat, dan IdP fixture yang membuat
key RSA sementara serta memeriksa PKCE. Resource tes dibersihkan setelah selesai.

SSO provider nyata belum dikonfigurasi; tanpa issuer/client registration endpoint
SSO mengembalikan 503 secara sengaja. Tes protokol lokal bukan sertifikasi kepatuhan
OIDC dan bukan verifikasi login ke Google/Microsoft/Keycloak produksi.

Image Docker v0.1 sebelumnya berhasil dibangun dan API container berjalan non-root.
Image/seluruh stack Compose **v0.2 belum dibangun ulang** karena keterbatasan disk
lingkungan Docker VFS 8 GB. Konfigurasi Compose v0.2 valid dan integrasi proses
nyata lulus; lakukan `docker compose up --build` pada host tujuan dengan ruang cukup.

Belum benchmark beban besar, HA/failover cluster, audit penetrasi, atau migrasi
MongoDB lama. Migrasi yang diuji adalah schema foundation PostgreSQL v0.1 → v0.2.
