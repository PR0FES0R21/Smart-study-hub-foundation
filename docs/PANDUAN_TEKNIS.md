# Panduan teknis Smart Study Hub Foundation v0.3.1

Dokumen ini menjelaskan apa yang dipakai, alasan pemilihannya, dan batasnya.
Fondasi ini mendukung beberapa sekolah SMP, SMA, dan SMK dalam satu backend.
Fitur contoh membuktikan login, CRUD, isolasi sekolah, pekerjaan background, SSO,
dan notifikasi. Ini belum aplikasi sekolah lengkap atau hasil audit keamanan independen.

## Peta teknologi dan kegunaannya

Versi paket Python yang persis dikunci berada di [`uv.lock`](../uv.lock).
Rentang kompatibilitasnya berada di [`pyproject.toml`](../pyproject.toml).

| Komponen | Spesifikasi / metode | Kegunaan dan contoh |
|---|---|---|
| Python | 3.12+ | Bahasa API, worker, dan alat operasional. |
| FastAPI + Uvicorn | FastAPI <1; Uvicorn <1, ASGI | HTTP API, validasi input, OpenAPI, dan WebSocket native. I/O API berjalan async. |
| Pydantic Settings | 2.x, konfigurasi dari environment | Validasi konfigurasi ketika startup; secret tidak ditanam dalam kode. |
| PostgreSQL | 16 | Sumber kebenaran: user, sekolah, membership, mata pelajaran, session, job, outbox. Transaksi dan constraint menjaga konsistensi. |
| SQLAlchemy + asyncpg | SQLAlchemy 2.0, asyncpg <1 | ORM async dan pool koneksi API. Satu session per pekerjaan/request; engine dipakai ulang dan ditutup saat shutdown. |
| SQLAlchemy + psycopg | SQLAlchemy 2.0, psycopg 3 | Akses sinkron dari child process Celery. Pool dibuat sesudah fork, sehingga koneksi tidak diwariskan lintas proses. |
| Alembic | 1.x | Riwayat perubahan schema yang dapat ditinjau. Migrasi release dijalankan satu kali sebelum aplikasi baru. |
| Redis cache | Redis 7.4, `allkeys-lru`, 128 MB awal | Cache-aside statistik mata pelajaran, TTL 30 detik. Cache boleh terbuang; PostgreSQL tetap menyimpan data asli. |
| Redis control | Redis 7.4, `noeviction`, AOF | Rate limit login, ticket WebSocket, state OIDC, validasi session singkat, heartbeat, dan Pub/Sub. Kegagalannya dapat menolak auth/WS secara aman. |
| Redis broker | Redis 7.4, `noeviction`, AOF everysec | Antrean pesan Celery. Dipisah agar pembersihan cache tidak membuang antrean atau kontrol keamanan. |
| redis-py | >=5.2,<7 | Client async untuk API dan sinkron untuk worker, dengan batas pool dan timeout. Pub/Sub memakai koneksi tersendiri. |
| Celery | >=5.5,<6, worker prefork | Menjalankan pekerjaan di luar request: contoh laporan mata pelajaran, dispatch outbox, rekonsiliasi job, dan cleanup. Cocok untuk task sinkron/CPU; task tidak memblokir event loop API karena berjalan di proses lain. |
| Celery beat | Satu proses per environment, UTC | Menjadwalkan dispatcher setiap 2 detik, rekonsiliasi setiap 60 detik, cleanup harian 03:15 UTC. Ini berbeda dari scheduler backup server. |
| Transactional outbox | Record event di transaksi PostgreSQL yang sama | Perubahan data dan niat mengirim job/event tersimpan bersama. Dispatcher mengirim ulang jika layanan tujuan gagal. |
| Native WebSocket | ASGI, ticket sekali pakai 30 detik | Mengirim `job.completed` kepada user/sekolah yang benar. Redis Pub/Sub menghubungkan worker dengan beberapa proses API. |
| JWT + refresh token | Access berumur pendek, refresh dirotasi | Login lokal; refresh token hanya disimpan sebagai digest. Replay mencabut session. Password memakai Argon2. |
| OIDC SSO | Authorization Code + PKCE S256, state/nonce, RS256/JWKS | Dasar login lewat IdP seperti Keycloak. Mapping issuer+subject eksplisit; email yang sama tidak otomatis menggabungkan akun. |
| HTTPX | Async client dengan pool, deadline, redirect nonaktif | Dasar koneksi keluar ke API pihak ketiga. Endpoint integrasi dipilih melalui konfigurasi, bukan URL bebas pengguna. |
| Logging JSON | Python logging + context request/task | Mencari error dengan `X-Request-ID`; mencatat aksi keamanan tanpa password atau isi request. Docker merotasi file log. |
| Prometheus | Server v3.5.0; prometheus-client <1 | Metrik jumlah request, latensi, dependency, outbox, heartbeat worker/beat, WebSocket. Endpoint internal dilindungi token. |
| Alertmanager | v0.28.1 | Mengelompokkan alert API/dependency mati, antrean tertahan, worker/beat berhenti, error 5xx. Receiver bawaan hanya UI privat; penerima eksternal perlu dikonfigurasi. |
| Restic | v0.19.1 + pg_dump/pg_restore 16 | Backup PostgreSQL ke repository terenkripsi, retensi, pemeriksaan integritas, dan latihan restore. Dapat memakai object storage S3 kompatibel di luar VPS. |
| systemd timers | Harian/mingguan; watchdog tiap menit | Menjalankan backup dan pengecekan API/disk/umur backup secara independen dari Celery. Backup tetap bisa berjalan saat worker aplikasi rusak. |
| Docker Compose | >=2.24.4 untuk override produksi | Menjalankan layanan dengan konfigurasi, volume, healthcheck, dan jaringan yang konsisten. Docker bukan backup dan bukan syarat FastAPI. |
| Caddy | 2.x | Reverse proxy HTTPS dan sertifikat otomatis pada domain produksi; membatasi body dan akses endpoint operasional. |
| uv, Ruff, pytest, GitHub Actions | Dependency lock, lint, unit/integrasi | Membuat lingkungan dapat diulang dan memeriksa perubahan sebelum dipakai. CI memerlukan runner GitHub yang aktif. |

## Alur yang perlu dipahami

### Request CRUD

Client mengirim access token dan `X-School-ID`. API memverifikasi user, session,
membership, dan role. Service membuka transaksi SQLAlchemy; PostgreSQL membatasi
baris tenant melalui Row Level Security (RLS). Constraint/FK juga menolak relasi
lintas sekolah. Header sekolah saja tidak memberikan hak akses.

Data umum disimpan dalam model bersama. Program keahlian hanya untuk SMK;
aturan ini diperiksa di API dan constraint database. Detail domain ada di
[panduan multi-sekolah](multischool-sso.md).

### Job Celery dan notifikasi

1. API menerima permintaan laporan dengan `Idempotency-Key`.
2. Job dan outbox disimpan dalam satu transaksi PostgreSQL, lalu API menjawab 202.
3. Dispatcher mempublikasikan task ke broker; worker membaca data sekolahnya.
4. Hasil job dan event penyelesaian disimpan dalam transaksi database.
5. Dispatcher mempublikasikan event ke Redis control; subscriber API mengirimnya ke WebSocket user tersebut.

Pengiriman bersifat **at least once**: pesan bisa sampai lebih dari sekali.
Karena itu handler job harus idempoten, memakai transaksi/locking, dan client
mendeduplikasi `event_id`. Jangan mengklaim exactly once. Pemanggilan pihak ketiga
yang menghasilkan pembayaran/email memerlukan idempotency dan strategi retry
khusus; contoh laporan tidak otomatis menyelesaikan masalah itu.

Pub/Sub tidak menyimpan riwayat. Setelah WebSocket reconnect, client mengambil
status job lewat HTTP. Gunakan outbox dan job persisten untuk keadaan penting;
Redis Streams atau broker lain baru diperlukan jika kebutuhan replay/throughput
nantinya menuntutnya. Microservice belum diperlukan untuk menjalankan fondasi ini.

### Cache, persistensi, dan backup

Cache menyimpan hasil sementara untuk mempercepat baca. Redis AOF membantu Redis
bertahan dari restart. Backup menyimpan salinan yang bisa dipulihkan setelah data
terhapus, rusak, atau VPS hilang. Ketiganya menyelesaikan masalah berbeda.

Invalidasi cache memakai generation token acak per sekolah dan outbox; token tidak
diulang ketika key terhapus oleh eviction. TTL membatasi
masa data usang ketika pengiriman invalidasi terganggu. Cache gagal tidak boleh
menghilangkan data CRUD. PostgreSQL tetap dibaca untuk operasi yang perlu data pasti.

Backup harian memakai snapshot konsisten `pg_dump` seluruh database, mencatat
checksum, lalu mengirim ke Restic terenkripsi. Lock mencegah backup/maintenance
berjalan bersamaan. Restore menolak database asal, nama target tanpa akhiran
`_restore_test`, dan target yang sudah berisi objek.

## Keamanan dan operasi yang sudah diimplementasikan

| Praktik | Implementasi dan batas |
|---|---|
| Least privilege | Role runtime tanpa SUPERUSER/BYPASSRLS; role migrasi dan backup terpisah. Role backup baca seluruh tenant karena backup harus lengkap. |
| Pengelolaan secret | File lokal diabaikan Git/Docker build context; generator acak; secret backup dibaca dari file. Untuk VPS simpan di `/etc/studyhub` berizin ketat atau secret manager. |
| Logging aman | Route pattern, request ID, event/task ID; tidak menyimpan request body. Redaksi credential dan penekanan exception mentah mengurangi kebocoran parameter SQL. UUID aktor tetap data sensitif. |
| Audit dasar | Login diterima/ditolak, replay refresh, logout, pembuatan user, perubahan subject. Ini log operasional; belum audit semua domain atau penyimpanan immutable. |
| Monitoring | Token metrik terpisah; label tidak berisi ID user/sekolah. Heartbeat dispatcher mendeteksi jalur beat→broker→worker→database, bukan sekadar proses API hidup. |
| Retensi | Log Docker maksimal 3 file ×10 MB per container; backup 7 harian/4 mingguan/6 bulanan; metrik 15 hari atau 1 GB. |
| Cleanup database | Maksimal 500 session yang kedaluwarsa >30 hari dan 500 outbox delivered >14 hari per hari. Session aktif, record replay-nya, outbox pending, job/idempotency, dan data akademik dipertahankan. Query retensi diberi index. |
| Batas sumber daya | Pool/timeout DB/Redis/HTTP, pagination, body limit, rate limit login, deadline task, antrean WebSocket terbatas. Angka awal perlu diuji terhadap beban nyata. |
| HTTPS dan jaringan | Override produksi menutup port datastore ke host; Caddy memberi TLS. Hanya IP proxy yang dipercaya untuk forwarded headers. Firewall dan SSH VPS tetap perlu dikonfigurasi. |
| Healthcheck | Liveness membuktikan proses hidup; readiness membuktikan dependency utama tersedia. Cache boleh degraded. Worker/beat dipantau terpisah. |

## Struktur yang membantu pengembangan

| Lokasi | Tanggung jawab |
|---|---|
| `src/studyhub/api/` | HTTP/WS, validasi dan dependency auth; jangan menaruh pekerjaan berat di route. |
| `src/studyhub/services/` | Use case, aturan bisnis, batas transaksi. |
| `src/studyhub/infrastructure/` | Factory koneksi DB/Redis, cache, Pub/Sub, metrik, outbound client. |
| `src/studyhub/core/` | Konfigurasi, keamanan, logging/context. |
| `src/studyhub/worker/` | Task, jadwal, resource per child, korelasi request→task. |
| `migrations/` | Perubahan schema terurut, termasuk index operasional. |
| `ops/` | Backup, restore, watchdog, proxy, monitoring, unit systemd, contoh grant DB. |
| `tests/` | Pembuktian unit dan integrasi dengan PostgreSQL/Redis/worker/API nyata. |

## Status penerapan dan keputusan lanjutan

Kode, konfigurasi, dan pengujian tersedia di repository. Timer otomatis,
repository backup offsite, domain HTTPS, dan notifikasi eksternal **belum aktif di VPS**:
host, domain, storage, dan penerima alert produksi belum disediakan.
Backup/restore diuji dengan repository Restic lokal terenkripsi dan database tes
terpisah; itu membuktikan mekanisme, bukan konektivitas ke bucket produksi.

Target awal backup adalah kehilangan data paling banyak sekitar 24 jam (RPO)
jika backup terjadwal berhasil. Waktu pemulihan (RTO) belum ditetapkan sebelum
diukur memakai ukuran data dan host nyata. Jika sekolah perlu pemulihan ke menit
tertentu, tambahkan WAL archiving/PITR melalui layanan PostgreSQL terkelola atau
pgBackRest/WAL-G; backup harian ini belum menyediakan PITR atau HA.

Satu VPS masih merupakan titik kegagalan bersama. Monitor di VPS yang sama tidak
bisa memberi alert ketika seluruh VPS mati; siapkan uptime monitor eksternal.
Saat skala meningkat ukur kapasitas dahulu, kemudian evaluasi PgBouncer,
replica, cluster, distributed tracing, dan pemisahan layanan berdasarkan kebutuhan.

Lanjutkan ke [backup dan restore](BACKUP_DAN_RESTORE.md),
[operasional VPS](OPERASIONAL_VPS.md), [perbaikan audit dan upgrade v0.3.1](SECURITY_FIXES_0_3_1.md),
dan [hasil verifikasi](verification.md).


## Kolaborasi akademik (v0.4.0)

Domain `academics` mengatur semester, penugasan mengajar dan penunjukan koordinator
oleh admin sekolah. Domain `assessments` mengatur soal, review, modul bersama,
versi terbit yang tidak dapat diubah dan konfigurasi ujian per kelas. PostgreSQL
menjaga relasi sekolah, keunikan, transaksi, RLS dan riwayat audit. Role koordinator
dibatasi pada mapel/tingkat/periode, sehingga satu guru bisa memiliki tanggung jawab
yang berbeda pada kelompok lain.

Baca [panduan kolaborasi ujian](KOLABORASI_UJIAN.md) untuk alur lewat endpoint,
contoh payload, alasan penggunaan row lock dan snapshot JSONB, serta batas fitur
yang sudah tersedia. Celery tetap untuk pekerjaan latar belakang; operasi review
dan publikasi ini diselesaikan dalam transaksi PostgreSQL agar hasilnya langsung
konsisten. Data otorisasi dan kunci soal tidak disimpan pada cache bersama.


## Riwayat siswa dan pengerjaan ujian (v0.5.0)

Profil siswa melekat pada sekolah, sedangkan enrollment merekam kelas per semester.
Kenaikan kelas menutup enrollment asal dan membuat yang baru; kelulusan menyimpan
status/tanggal tanpa menghapus riwayat. SMP/SMA/SMK memakai tingkat kelulusan yang
divalidasi database, termasuk SMK kelas 13.

Jawaban ujian disimpan di PostgreSQL dengan revisi untuk mencegah overwrite dari
dua perangkat. Tenggat dihitung server, dan Celery menutup sesi kedaluwarsa saat
siswa putus koneksi. Transactional outbox meneruskan notifikasi privat melalui
Redis Pub/Sub dan WebSocket. Pilihan ganda dinilai otomatis, esai oleh guru; nilai
ditahan sampai rilis eksplisit. Respons autosave ringkas, tanpa paket soal ulang.

Lihat [spesifikasi, endpoint, aturan dan cara upgrade](SISWA_DAN_PELAKSANAAN_UJIAN.md).
