# Keamanan

Perbaikan audit v0.3.1 beserta migrasi pencabutan sesi akun tanpa password lokal
dijelaskan dalam [catatan rilis keamanan](SECURITY_FIXES_0_3_1.md).

- Password Argon2id, proses hashing/verifikasi di thread agar event loop API tidak
  terblokir. Login unknown-user tetap memverifikasi dummy hash. Akun tanpa hash
  password lokal selalu ditolak oleh login password. Error kredensial generik.
- JWT HS256 hanya menerima algorithm, issuer, audience dan type yang ditetapkan;
  access token 15 menit. Setiap request memeriksa session aktif di PostgreSQL.
- Refresh token acak, hanya digest SHA-256 disimpan. Rotasi memakai session row lock;
  reuse token lama mencabut seluruh session. Client wajib menserialkan refresh:
  dua refresh serentak pada token sama memang dianggap replay dan mencabut session.
- Logout mencabut session; WebSocket memeriksanya berkala maksimal sekitar 20 detik
  saat dependency sehat. Tidak mengandalkan cache role/session untuk otorisasi.
- Tidak ada default admin, signup publik, atau endpoint promosi menjadi platform admin.
  Provisioning admin melalui CLI dengan getpass. Schema respons tidak mengandung hash.
- Role diperiksa di server; pemilik job dibatasi pada query, unauthorized object 404.
  Model tidak menerima mass assignment dari input, parameter SQL di-bind SQLAlchemy.
- Login/refresh memakai rate limit atomik Redis per IP dan identitas. Jika control
  gagal, autentikasi baru ditolak sementara (fail closed); cache analitik fail open.
- WebSocket memakai ticket opaque 30 detik, dikonsumsi `GETDEL`, tidak memakai JWT
  di URL. Native client tanpa Origin diizinkan; Origin browser harus allowlist.
  Ticket tetap credential: reverse proxy juga harus membuang query string dari log.
- Per-process limit tiga koneksi per user, queue 64 pesan, send timeout lima detik,
  termasuk koneksi closing sampai cleanup selesai. Event diarahkan memakai index
  user/sekolah dan frame biner/JSON rusak ditutup dengan kode protokol yang sesuai.
  Batas protokol 1024 bytes diterapkan di command Docker, disertai batas pesan
  aplikasi dan rate per socket.
  Untuk batas global lintas replica, tambahkan connection lease terdistribusi.
- CORS eksplisit tanpa cookies, TrustedHost allowlist, no-store, nosniff, request ID.
  Body HTTP dibatasi 1 MiB, termasuk stream tanpa Content-Length.
  Validation error menghilangkan nilai input agar password/token tidak terpantul.
- Outbound HTTP mempunyai base URL HTTPS yang dikonfigurasi server, koneksi/timeout
  dibatasi, redirect dimatikan. JSON IdP/integrasi dibatasi 256 KiB dan deadline
  total 5 detik; encoding terkompresi ditolak. Tidak ada endpoint proxy URL bebas.
- Job baru dibatasi per user/sekolah dan kapasitas pending. Transaction advisory
  lock menserialkan pemeriksaan kapasitas; retry key yang sudah ada tidak membuat
  job tambahan meskipun rate quota telah habis.

## Sebelum produksi

Compose adalah lingkungan development yang dapat dijalankan, bukan sertifikasi
production-ready. PostgreSQL bootstrap/migrator development memakai role terpisah untuk membuat schema
dan role runtime terbatas. Tes juga menjalankan API/worker dengan role terbatas
agar RLS efektif; produksi wajib memisahkan role migrator dari role runtime tanpa DDL,
tanpa SUPERUSER/CREATEDB/CREATEROLE. Batasi grants hanya tabel aplikasi.

Pakai secret manager, secret JWT acak minimal 32 karakter (generator membuat lebih
panjang), kredensial unik, backup teruji, TLS ingress dan koneksi database/Redis jika
melewati jaringan yang tidak dipercaya. Jangan expose port datastore ke publik;
port Compose hanya bind loopback dan dapat dihapus pada deployment server.

Redis production perlu ACL: cache boleh GET/SET/INCR/EVAL pada namespace cache;
control perlu ticket GETDEL, rate EVAL, PUBLISH/SUBSCRIBE pada channel aplikasi;
broker membutuhkan perintah Kombu/Celery. Tolak perintah administrasi seperti FLUSHALL.
Jangan memakai kebijakan eviction cache untuk antrean atau tiket autentikasi.

Atur environment=production, host/origin HTTPS yang spesifik, proteksi OpenAPI di
ingress bila diperlukan (Swagger dimatikan pada produksi). Proxy headers default
dimatikan; saat di belakang ingress, trust hanya alamat proxy yang diketahui dan
samakan trusted-forwarded-IP dengan konfigurasi rate limiting. Terapkan ingress
body limit, request timeout, TLS/HSTS, connection limits dan pembatasan login tambahan.

JWT key rotation perlu strategi deployment tersendiri (misalnya OIDC/JWKS dengan kid).
OIDC authorization-code SSO tersedia dengan provisioning eksplisit; lihat
[multi-sekolah/SSO](multischool-sso.md). Inbound webhook, API key mesin dan password reset belum diekspos. Lakukan
threat modelling per fitur, dependency scanning, serta audit sebelum menyimpan data siswa nyata.
