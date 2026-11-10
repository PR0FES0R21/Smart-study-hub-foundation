# Menjalankan dan merawat layanan di VPS

Panduan ini melengkapi [peta teknologi](PANDUAN_TEKNIS.md) dan
[backup/restore](BACKUP_DAN_RESTORE.md). Konfigurasi adalah titik awal satu VPS;
kapasitas, uptime dan pemulihan harus diuji sesuai kebutuhan sekolah.

## Persiapan host dan konfigurasi

Gunakan Linux yang masih didukung, Docker Engine/Compose >=2.24.4, systemd,
Python 3, disk dengan ruang cadangan, domain, dan akses object storage offsite.
Contoh path checkout: `/opt/studyhub`; konfigurasi privat: `/etc/studyhub`.
Gunakan SSH key dan akun admin terpisah; batasi SSH pada IP/VPN administrasi,
buka 80/443 untuk Caddy, dan jangan mempublikasikan PostgreSQL/Redis/monitoring.
Periksa firewall provider dan host. Akses Docker socket setara akses admin host.

Sebelum data nyata masuk, lakukan backup dan restore percobaan, aktifkan alert,
pastikan waktu server tersinkron, atur patch OS, dan catat siapa penanggung jawab
pemulihan. Jangan menaruh credential VPS/bucket di GitHub atau percakapan publik.

```bash
cd /opt/studyhub
python3 scripts/init_env.py
uv sync --frozen
uv run python scripts/init_ops.py
```

Generator tidak menimpa `.env` yang sudah ada. `init_ops.py` membuat token metrik
di `.env` dan `.secrets/metrics_token`, password Restic, serta contoh `.env.backup`;
hostname internal `api` ditambahkan ke allowed hosts untuk scrape Prometheus.
Token yang disimpan API dan file Prometheus harus sama. Parent `.secrets` privat
0700; file token dapat dibaca user container Prometheus. Jangan mount seluruh
direktori secret ke service monitoring.

Pindahkan konfigurasi aplikasi ke `/etc/studyhub/app.env` (0600), sesuaikan:

```dotenv
STUDYHUB_ENVIRONMENT=production
STUDYHUB_NAMESPACE=studyhub:production
STUDYHUB_DOMAIN=api.sekolah.example.org
STUDYHUB_ALLOWED_ORIGINS=["https://app.sekolah.example.org"]
STUDYHUB_ALLOWED_HOSTS=["api.sekolah.example.org","api","localhost","127.0.0.1"]
```

Domain di atas contoh; pakai domain milik sendiri dengan DNS menunjuk VPS.
Pertahankan password acak dan variabel lain dari generator. Jangan gunakan wildcard
origin/host. Sesuaikan issuer/audience JWT, registrasi OIDC dan callback HTTPS
sebelum mengaktifkan SSO. Untuk akses metrik pindahkan token ke file privat dan
set `STUDYHUB_METRICS_TOKEN_PATH` pada `app.env` agar mount mengikuti lokasi VPS.

`compose.production.yaml` menutup port PostgreSQL/Redis pada host. Caddy melayani
HTTPS dan memblokir `/internal/*`, readiness, docs, serta OpenAPI dari internet.
API masih tersedia pada loopback `127.0.0.1:8000` untuk watchdog/admin lokal.
Forwarded headers dipercaya hanya dari IP Caddy `172.29.0.2`; bila subnet
`172.29.0.0/24` bentrok, ubah subnet, IP kedua service dan trusted proxy bersamaan.
Port publik dan HTTPS aktif hanya setelah stack dijalankan di host/domain nyata.

## Database dan startup pertama

Role migrasi mengubah schema; role runtime membaca/mengubah data sesuai RLS.
Mode produksi sengaja tidak menjalankan provisioning development otomatis.
Untuk instalasi pertama, jalankan migrasi dahulu:

```bash
docker compose --env-file /etc/studyhub/app.env -f compose.yaml -f compose.production.yaml up -d postgres cache control broker
docker compose --env-file /etc/studyhub/app.env -f compose.yaml -f compose.production.yaml build api
docker compose --env-file /etc/studyhub/app.env -f compose.yaml -f compose.production.yaml run --rm migrate
```

Buka `psql` sebagai admin PostgreSQL, jalankan
[`ops/database/runtime-role.sql`](../ops/database/runtime-role.sql) sekali,
lalu `\password studyhub_app` secara interaktif. Gunakan password yang sama dengan
`APP_DATABASE_PASSWORD` privat. Role migrasi/pemilik tabel contoh adalah `studyhub`;
ubah SQL grant/default privileges bila nama role atau database berbeda.
Pada database yang sudah memiliki role, tinjau privilege dan gunakan ALTER/GRANT
yang sesuai; jangan menghapus role/tabel agar script bisa dijalankan ulang.

```bash
docker compose --env-file /etc/studyhub/app.env -f compose.yaml -f compose.production.yaml up -d api worker beat caddy
docker compose --env-file /etc/studyhub/app.env -f compose.yaml -f compose.production.yaml exec api python -m studyhub.cli create-admin --email admin@sekolah.example.org --name Admin
curl --fail http://127.0.0.1:8000/health/ready
```

CLI meminta password secara interaktif, tidak ada password admin bawaan.
Buat sekolah/membership melalui API sesudah login. Role runtime harus
NOSUPERUSER/NOBYPASSRLS dan bukan role pemilik tabel. API menolak role runtime
privileged saat startup produksi; grant pemilik schema harus tetap ditinjau.
Untuk database eksternal, sesuaikan Compose URL dengan endpoint/TLS provider;
Compose dasar ini memang ditujukan untuk PostgreSQL dalam jaringan lokal stack.

## Logging: apa yang dicatat dan bagaimana dibaca

API menulis JSON ke stdout. Setiap request mendapat `X-Request-ID` acak dari server;
client dapat menyertakannya saat melaporkan masalah. Request ID diteruskan melalui
outbox ke header task Celery, lalu log task dan event penyelesaian.
Log route memakai pola seperti `/api/v1/subjects/{subject_id}`, bukan query string.

```bash
docker compose logs --since 15m --tail 200 api worker beat
journalctl -u studyhub-backup@backup.service --since today --no-pager
journalctl -u studyhub-watch.service --since today --no-pager
```

Audit dasar mencatat aksi login/refresh replay/logout, user dibuat, subject
disimpan/dihapus, beserta UUID resource yang berubah. Aktor/sekolah berasal dari
konteks terverifikasi. Password,
access/refresh token, ticket, code OIDC, body request, dan raw exception SQL tidak
dimaksudkan masuk log. Redaksi adalah perlindungan tambahan; jangan menulis
credential/PII ke log baru. Error HTTP mengembalikan pesan umum agar parameter DB
tidak bocor. Penekanan traceback mengurangi detail diagnosis; reproduksi dengan
data tes atau gunakan pelaporan error privat yang telah diberi sanitasi bila dibutuhkan.

Docker membatasi log per container ke 3 file ×10 MB. Batas ini berdasarkan ukuran,
bukan jaminan jumlah hari. UUID user/sekolah tetap bisa mengidentifikasi seseorang;
batasi akses dan tetapkan retensi institusi. Log lokal dapat hilang bersama VPS
dan dapat diubah admin. Untuk kebutuhan audit kuat, kirim ke collector terpisah
dengan kontrol akses/retensi/immutability; itu belum disertakan.

## Monitoring dan alert

Liveness `/health/live` hanya memeriksa proses. Readiness `/health/ready` memeriksa
database/schema, control, broker, subscriber; kegagalan cache ditandai degraded.
Readiness tidak membuktikan worker bekerja. Heartbeat dispatcher diperbarui
setelah task dari beat diproses worker, bertransaksi ke DB dan mengakses Redis.

```bash
docker compose --env-file /etc/studyhub/app.env -f compose.yaml -f compose.production.yaml -f compose.monitoring.yaml --profile monitoring up -d api prometheus alertmanager
```

Prometheus tersedia hanya pada loopback 9090, Alertmanager 9093. Gunakan SSH port
forwarding untuk melihat UI. `/internal/metrics` perlu bearer token; tanpa token
konfigurasi endpoint nonaktif. Token metrik terpisah dari JWT user.
Metrik menghitung method/route/status, durasi HTTP, dependency up/down, backlog
outbox, attempts, umur event tertua, heartbeat dan koneksi WS lokal. Tidak memakai
ID tenant/user sebagai label agar tidak membocorkan identitas dan membengkakkan seri.
Gunakan satu Uvicorn per container; metrik HTTP/WS per proses. Untuk beberapa
replica, daftarkan setiap target atau gunakan service discovery.

Alert bawaan:

| Kondisi | Ambang awal |
|---|---|
| API tidak bisa di-scrape atau dependency mati | 2 menit |
| Heartbeat dispatcher basi | >90 detik, bertahan 2 menit |
| Event outbox belum terkirim | Umur >5 menit, bertahan 5 menit |
| Proporsi 5xx | >5% selama 5 menit |

Receiver bawaan **hanya menampilkan alert pada UI privat**. Buat konfigurasi
Alertmanager privat untuk email/webhook pilihan, lalu tunjuk dengan
`STUDYHUB_ALERTMANAGER_CONFIG`. Jangan commit URL bertoken/password.
Uji penerimaan dan recovery alert pada receiver nyata sebelum mengandalkannya.
Retensi Prometheus 15 hari atau 1 GB; sesuaikan ruang dan kebutuhan.

Watchdog host menambahkan pemeriksaan disk <10% kosong, umur backup >26 jam,
operasi backup gagal, dan readiness API. Salin `ops/backup/watch.env.example` ke
`/etc/studyhub/watch.env`, isi webhook HTTPS opsional melalui file secret,
lalu pasang unit:

```bash
sudo install -m 644 ops/systemd/studyhub-watch.service /etc/systemd/system/
sudo install -m 644 ops/systemd/studyhub-watch.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now studyhub-watch.timer
sudo systemctl start studyhub-watch.service
```

Webhook berbentuk JSON `{"text":"..."}`; sesuaikan receiver. Watchdog mengirim saat
status berubah dan mengulang kegagalan tiap 30 menit. Exit 1 saat gangguan sengaja
menandai service gagal di systemd; timer menjalankannya lagi. Alert tidak terkirim
ke mana pun tanpa receiver yang dikonfigurasi. Tambahkan uptime monitor **di luar
VPS** untuk mendeteksi host/jaringan mati total.

## Perawatan dan perubahan versi

Jalankan tepat satu beat per environment. Cleanup harian menghapus maksimal 500
session yang telah kedaluwarsa lebih dari 30 hari dan 500 outbox delivered lebih
dari 14 hari. Refresh token terpakai dipertahankan selama session masih aktif
untuk deteksi replay. Pending outbox, job/idempotency dan data akademik tidak dihapus.
Pantau pertumbuhan tabel; batch 500/hari perlu dinaikkan jika laju data lebih besar.

Sebelum rilis: pastikan backup terbaru sukses dan restore telah dilatih, baca
perubahan migrasi, jalankan tes, bangun image, lakukan satu migrasi release, lalu
perbarui API/worker/beat dan periksa health/job/WS. Gunakan perubahan schema
expand/contract jika versi lama dan baru berjalan bersamaan. Rollback source
code tidak otomatis membatalkan perubahan data; jangan melakukan downgrade DB
sembarangan. Index baru biasa pada v0.3 dapat mengunci write sementara saat migrasi;
untuk tabel besar gunakan strategi concurrent index dalam rilis khusus.

Pantau koneksi PostgreSQL (`pg_stat_activity`), ukuran tabel, deadlock/query lambat,
Redis memory/rejected writes, usia outbox dan disk. Exporter khusus PostgreSQL/Redis,
tracing OpenTelemetry dan dashboard Grafana belum dipasang. Baseline ini memberi
metrik aplikasi; ia bukan pemantauan mendalam seluruh host/database.

Worker diberi graceful shutdown 180 detik; deadline task 150 detik dan visibility
timeout broker 300 detik. API diberi 40 detik untuk shutdown. Task harus idempoten
karena worker crash bisa menyebabkan redelivery. Jangan mengirim object Python
arbitrer ke broker; serializer hanya JSON. Upgrade dependency terjadwal, periksa
advisory dan tinjau perubahan lockfile/image sebelum deploy. Image version tags
masih perlu diperbarui dan dapat dipin digest melalui pipeline produksi.

Daftar hal yang harus selesai sebelum peluncuran data nyata: domain/TLS, role dan
secret yang benar, backup offsite terjadwal + restore lulus, receiver alert teruji,
akses operator terbatas, serta hasil uji beban dan kebijakan privasi sekolah.
