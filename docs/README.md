# Peta dokumentasi dan status penerapan

Dokumentasi ini mengikuti fondasi v0.6.0. Kode yang tersedia, hasil pengujian lokal,
dan konfigurasi yang sudah aktif di server adalah tiga status yang berbeda.
Keberadaan panduan atau file konfigurasi tidak berarti layanan produksi sudah aktif.

## Mulai membaca

| Kebutuhan | Dokumen |
|---|---|
| Teknologi, metode, alasan dan kegunaan setiap komponen | [Panduan teknis](PANDUAN_TEKNIS.md) |
| Koneksi, transaksi, cache, outbox, idempotensi dan Pub/Sub | [Arsitektur](architecture.md) |
| Akun, token, RBAC, WebSocket, batas request dan keamanan produksi | [Keamanan](security.md) |
| Sekolah SMP/SMA/SMK, membership dan SSO OIDC | [Multi-sekolah dan SSO](multischool-sso.md) |
| CRUD, kamus data, tahun/semester, wali kelas dan aturan penghapusan | [CRUD dan struktur data](CRUD_DAN_STRUKTUR_DATA.md) |
| Guru, penugasan, koordinator, soal dan modul bersama | [Kolaborasi ujian](KOLABORASI_UJIAN.md) |
| Enrollment, kenaikan kelas, kelulusan, pengerjaan dan penilaian ujian | [Siswa dan pelaksanaan ujian](SISWA_DAN_PELAKSANAAN_UJIAN.md) |
| VPS, HTTPS, logging, monitoring, alert dan perawatan | [Operasional VPS](OPERASIONAL_VPS.md) |
| Satu VPS aplikasi + satu Redis + satu PostgreSQL; kesiapan beberapa replica | [Deployment tiga VPS](DEPLOYMENT_TIGA_VPS.md) |
| Backup terenkripsi, retensi, penjadwalan dan latihan restore | [Backup dan restore](BACKUP_DAN_RESTORE.md) |
| Health, diagnosis dan perilaku saat dependency gagal | [Operasi](operations.md) |
| Dasar integrasi pihak ketiga dan batas yang belum tersedia | [Integrasi](integrations.md) |
| Temuan audit lama, perbaikan dan migrasinya | [Perbaikan keamanan v0.3.1](SECURITY_FIXES_0_3_1.md) |
| Bukti pengujian dan keterbatasannya per rilis | [Hasil verifikasi](verification.md) |

Versi paket persis tersedia di `uv.lock`, rentang dependensi di `pyproject.toml`,
perubahan schema di `migrations/`, konfigurasi operasional di `ops/`, dan kontrak
endpoint di OpenAPI yang dihasilkan aplikasi. Jika menambah fitur, perbarui dokumen
domain dan bukti verifikasinya bersama perubahan source.

## Status yang perlu dipahami

| Area | Sudah tersedia/diuji | Masih perlu dilakukan di server tujuan |
|---|---|---|
| API dan data | FastAPI, ORM async, transaksi, constraint, RLS, CRUD dan alur akademik | Migrasi, grant runtime, konfigurasi kapasitas dan restart proses saat release |
| Celery dan Redis | Worker terpisah, satu beat, outbox, rekonsiliasi, cache dan Pub/Sub; diuji lintas dua proses API | Endpoint privat, autentikasi/ACL, persistence, resource limit, pemantauan dan jumlah worker |
| Autentikasi/otorisasi | Argon2, session, rotasi refresh, membership/RBAC, OIDC dengan IdP lokal | Domain/secret/IdP produksi, akses operator dan prosedur rotasi credential |
| Race condition | Row lock, revisi, unique key dan idempotensi pada alur terkait; tes concurrency lokal | Uji topologi nyata, timeout/retry dan kegagalan jaringan; idempotensi untuk fitur baru |
| Backup | Restic + PostgreSQL dump, timer, retensi, restore ke DB terpisah diuji lokal | Bucket offsite, secret, aktivasi timer, receiver alert dan latihan restore terukur |
| Logging/monitoring | JSON log, request ID, redaksi, audit akademik, metrik dan aturan alert | Retensi institusi, receiver nyata, pemantauan eksternal dan kapasitas disk |
| Jaringan | Contoh Caddy/Compose produksi dan pemisahan role | Private network/VPN, firewall, TLS, DNS serta konfigurasi deployment lintas host |
| Pengujian | Suite v0.6.0: 107 tes lokal lulus | Build/smoke test pada server tujuan dan load/failure test; CI terakhir gagal mulai (`startup_failure`) |

Belum ada jaminan bahwa seluruh kemungkinan bug atau OWASP Top 10 sudah tertutup.
Pengujian fungsional bukan audit penetrasi independen. MFA, reset password global,
HA/failover otomatis, PITR, distributed tracing, kuota WebSocket global dan fitur
integrasi mesin lengkap belum disediakan. Kebutuhannya dinilai sesuai fitur dan
target operasional, bukan diasumsikan aktif karena ada di roadmap.

Setiap deployment sebaiknya mencatat tanggal, commit/image, penanggung jawab,
hasil pemeriksaan, dan bukti restore/alert di catatan operasi privat. Jangan mengisi
password, token, DSN berkredensial atau data siswa dalam checklist GitHub.
