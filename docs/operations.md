# Operasional

## Koneksi dan kapasitas

Default development API: pool_size=5, overflow=5, pool_timeout=5 detik;
worker child: dua koneksi tanpa overflow. Angka ini anggaran awal untuk stack lokal,
bukan hasil benchmark. Perkirakan total koneksi = jumlah proses API × (pool+overflow)
+ jumlah child worker × pool + migrasi/operasional. Sisakan kapasitas administrasi.
Sesuaikan dengan pg_stat_activity, waktu tunggu pool, p95 query dan batas server.
Command/statement timeout API 30 detik; task berat harus memiliki deadline tersendiri.

Jalankan Alembic sebagai release job tunggal sebelum rolling update. Jangan memakai
metadata.create_all saat startup. Gunakan migrasi expand/contract untuk kompatibilitas
versi API lama dan baru. Validasi drift dengan `uv run alembic check` setelah upgrade.

## Kesehatan dan pengamatan

Liveness hanya memastikan proses hidup. Readiness memeriksa database beserta tabel
versi migrasi, control, broker, dan subscriber lokal; cache dilaporkan terpisah.
Readiness tidak membuktikan worker/beat hidup: pantau keduanya serta usia job pending.

Pantau minimal:
- HTTP error rate, latensi p95/p99, request ID.
- PostgreSQL pool wait/timeouts, jumlah koneksi, query lambat, deadlock.
- Redis memory, evicted_keys untuk cache, rejected writes untuk noeviction.
- Celery queue depth, worker heartbeat, task runtime/retry.
- Outbox pending count, usia tertua, attempts; alert bila terus naik.
- Subscriber reconnect dan jumlah WebSocket serta slow-consumer close.

Outbox yang gagal dicoba ulang dengan backoff maksimal 300 detik. Jika broker
kehilangan task setelah publish sukses (misalnya kehilangan AOF terakhir), job
pending dicari ulang oleh task reconciliation setiap 60 detik setelah berusia
minimal satu menit, kemudian dikirim ulang melalui outbox secara idempoten.
Jangan menganggap AOF everysec sebagai jaminan zero data loss. PostgreSQL job
tetap menyimpan permintaan yang diterima API.

Retensi: bersihkan outbox delivered dan auth session kadaluarsa secara terjadwal
dengan batch kecil. Simpan refresh token yang sudah used sampai session berakhir
agar deteksi replay tidak hilang. Jangan menghapus job sebelum masa retensi
idempotency key berakhir. Fondasi tidak menghapus data bisnis otomatis.

## Pengujian kegagalan dan deployment

Uji cache dihentikan (CRUD tetap bekerja), control dihentikan (auth baru/WS menolak,
readiness 503), worker dimatikan sesaat (job tetap pending), restart Redis control
(subscriber reconnect), dan duplicate delivery (hasil tetap satu).

Jalankan satu Uvicorn per container dan scale replica; setiap proses memiliki
subscriber lokal. Worker memakai prefork dan resource per child; jangan mengganti
ke gevent/eventlet tanpa evaluasi ulang. Task time limit harus lebih pendek dari
broker visibility timeout. Pastikan graceful termination lebih lama dari task aktif.

Build Docker di lingkungan dengan CA inspeksi dapat memakai BuildKit secret:
`docker build --secret id=proxy_ca,src=/path/to/combined-ca.pem -t studyhub .`.
CA tidak disalin ke image. Dependency dikunci oleh uv.lock dan sync memakai --frozen.

Gunakan managed PostgreSQL + backup/PITR sesuai kebutuhan institusi. Compose volume
bukan backup. Pemulihan database wajib diuji sebelum deployment data nyata.
