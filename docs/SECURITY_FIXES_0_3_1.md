# Perbaikan audit keamanan dan logika v0.3.1

Rilis ini menerapkan seluruh sepuluh perbaikan dari audit terhadap commit
`526f561`. Kandidat perbaikan telah diterapkan ke source aplikasi, disertai
regression tests dan migrasi. Ringkasan ini menjelaskan perilaku akhir; temuan
yang dibahas adalah kondisi versi sebelumnya, bukan klaim bahwa bug masih aktif.

| Temuan awal | Perbaikan yang diterapkan | Pembuktian |
|---|---|---|
| Critical: login lokal akun tanpa password dapat lolos melalui dummy hash | Verifikasi dummy tetap dijalankan untuk timing, tetapi session hanya terbit jika akun memiliki hash password lokal. | Akun SSO-only platform admin ditolak HTTP 401. |
| Medium: risiko privasi penautan akun global tanpa consent | Penautan memerlukan platform admin yang juga admin sekolah tujuan. Role admin sekolah saja tidak cukup. | Penautan oleh admin biasa ditolak, profil target tetap tidak terlihat. |
| Medium: penerimaan job baru tanpa batas | Rate limit 20/user/menit dan 100/sekolah/menit; maksimal 100 pending/sekolah. Advisory transaction lock dan pemeriksaan ulang idempotency key menserialkan admission per sekolah. | Request baru dibatasi; retry key lama tetap memperoleh job yang sama saat kuota habis; empat koneksi DB bersamaan hanya menerima satu job ketika tersisa satu slot. |
| Medium: generation cache dapat mengulang versi setelah eviction | Generation berupa token acak 128-bit; SET NX saat key hilang dan fallback ke DB jika tetap tidak tersedia. API dan dispatcher worker memakai protokol yang sama. | Eviction key generation tidak menghidupkan kembali statistik lama. |
| Medium: frame biner WebSocket memicu exception | Frame ASGI diperiksa sebelum parsing. Close code 1003 untuk biner, 1007 JSON rusak, 1009 terlalu besar; kegagalan DB/Redis ditangani. | Frame biner menutup koneksi secara terkontrol tanpa exception handler keluar. |
| Medium: slot koneksi dilepas sebelum socket benar-benar ditutup | Koneksi diberi status closing, backlog dibuang, sentinel dikirim segera. Kuota dilepas oleh cleanup endpoint. | Koneksi lambat masih dihitung sampai disconnect; index dan counter dibersihkan. |
| Medium: semua koneksi dipindai untuk setiap event | Index berdasarkan `(user_id, school_id)` dan counter per user. | Event satu penerima tidak memindai registry 2.500 koneksi. |
| Medium: respons upstream tidak dibatasi | Discovery/token/JWKS/capabilities menggunakan streaming JSON maksimal 256 KiB, deadline total 5 detik dan bentuk object. Encoding terkompresi ditolak pada jalur ini. | Respons stream yang melampaui budget ditolak; alur OIDC dengan IdP tes tetap berhasil. |
| Medium: upload manifest.json mengganggu restore DB | Manifest database diambil dari path staging eksplisit, terpisah dari nama file upload. | Backup terenkripsi dengan upload manifest.json tetap dapat dipulihkan. |
| Low: notifikasi recovery yang gagal tidak diulang | State observasi dipisah dari state notifikasi yang telah berhasil terkirim. | Webhook recovery yang gagal dicoba kembali pada pemeriksaan berikutnya. |

## Sesi yang sudah terbit sebelum perbaikan

Menutup login baru saja tidak membatalkan session yang mungkin sudah diterbitkan
melalui celah versi lama. Migrasi `e21aa6c09f52` mencabut **seluruh session yang
belum revoked milik akun tanpa password lokal (NULL atau kosong)**. Session login
lokal dan SSO berbagi tabel, sehingga session SSO sah pada akun tersebut juga ikut
dicabut satu kali. Pengguna dapat login lagi melalui IdP sesudah rilis.

Refresh token lama tidak bisa digunakan untuk mengaktifkan kembali session yang
dicabut. Downgrade migrasi sengaja tidak memulihkan session tersebut. Migrasi
tidak menghapus akun, membership, atau data akademik. Tes migrasi membuktikan
session akun dengan password lokal tidak ikut dicabut.

## Perubahan perilaku yang perlu diketahui client/operator

- Akun SSO-only menggunakan login IdP. Password dummy bukan credential akun.
- Admin sekolah tetap dapat membuat user baru teacher/student. Menautkan akun
  global yang sudah ada memerlukan platform admin dan membership admin pada
  sekolah tujuan. Alur undangan dengan consent belum tersedia.
- Job baru dapat menerima 429 dengan Retry-After 60 detik. Pertahankan
  Idempotency-Key saat mengulang permintaan yang sama. Kuota di atas merupakan
  angka awal dalam `api/routes.py`, bukan hasil benchmark kapasitas.
- Provider JSON upstream perlu mendukung `Accept-Encoding: identity` dan respons
  maksimal 256 KiB. Sesuaikan budget berdasarkan kontrak integrasi jika diperlukan;
  batas body HTTP masuk tidak mengatur respons upstream.
- Restore pada path host berbeda dapat menetapkan
  `STUDYHUB_RESTORE_SOURCE_STATE_DIR` ke path state saat snapshot dibuat, umumnya
  `/backup/state` pada container backup.
- Kuota WebSocket tetap tiga koneksi per user **per proses API**. Batas global
  lintas replica memerlukan connection lease terdistribusi tersendiri.

## Urutan upgrade dari v0.3

1. Pastikan backup terbaru tersedia, build image rilis baru, dan siapkan jeda layanan.
2. Hentikan API, worker dan beat lama. Worker lama menggunakan INCR generation,
   sedangkan versi baru menggunakan token acak; jangan campur kedua versi.
3. Jalankan migrasi dengan credential migrator: `alembic upgrade head` atau service
   Compose `migrate`. Migrasi menambah partial index untuk admission job dan
   mencabut session akun tanpa password lokal.
4. Jalankan API, worker dan beat dari rilis yang sama, lalu periksa readiness,
   login, job, WebSocket dan heartbeat. Pengguna SSO-only lama perlu login ulang.
5. Perbarui image backup serta file watchdog pada host agar perbaikan operasional
   juga aktif. Push source ke GitHub tidak otomatis memperbarui instalasi VPS.

Untuk Compose produksi dengan path runbook VPS, setelah image baru selesai dibangun:

```bash
docker compose --env-file /etc/studyhub/app.env -f compose.yaml -f compose.production.yaml stop api worker beat
docker compose --env-file /etc/studyhub/app.env -f compose.yaml -f compose.production.yaml run --rm migrate
docker compose --env-file /etc/studyhub/app.env -f compose.yaml -f compose.production.yaml up -d api worker beat
```

Sertakan override monitoring yang digunakan instalasi ketika menjalankan kembali
API agar konfigurasi token metrik tetap terpasang. Index biasa dapat menahan write
ketika dibuat; untuk tabel besar evaluasi migrasi index concurrent terpisah.

Lihat [hasil pengujian](verification.md), [multi-sekolah/SSO](multischool-sso.md),
[backup dan restore](BACKUP_DAN_RESTORE.md), dan [runbook VPS](OPERASIONAL_VPS.md).
Perbaikan ini bukan sertifikasi bebas celah, benchmark beban besar, atau audit CVE
seluruh dependency/image. Hasil CI GitHub dan aktivasi pada VPS dilaporkan terpisah
dari tes lokal.
