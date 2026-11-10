# Backup otomatis dan latihan restore

Backup melindungi data saat salah hapus, disk rusak, atau VPS hilang. Volume Docker,
Redis AOF, GitHub source code, dan replikasi bukan pengganti backup.
Implementasi ada di [`ops/backup/backup.py`](../ops/backup/backup.py).

## Kebijakan awal

| Hal | Ketentuan |
|---|---|
| Isi | Satu database PostgreSQL lengkap: semua sekolah, user, session, data akademik, job, outbox, schema/migrasi. |
| Metode | `pg_dump` format custom, validasi daftar arsip, manifest SHA-256, Restic terenkripsi. PostgreSQL memakai snapshot transaksi konsisten. |
| Tujuan | Repository Restic di object storage/server lain. Repository di disk VPS yang sama hanya cocok untuk percobaan. |
| Jadwal | systemd harian 02:00 UTC, jitter maksimal 10 menit; terlepas dari Celery. Timer persistent menjalankan jadwal yang terlewat ketika server kembali hidup. |
| Retensi | 7 snapshot harian, 4 mingguan, 6 bulanan; periode dapat saling tumpang tindih. Hanya host+tag backup ini yang dipilih. |
| Maintenance | Mingguan Minggu 04:00 UTC: periksa metadata + sampel 10% data repository, lalu prune. Jadwalkan pemeriksaan penuh berkala sesuai kapasitas. |
| Deteksi gagal | Exit nonzero, status JSON, jurnal systemd; watchdog tiap menit menandai backup >26 jam atau operasi terakhir gagal. Webhook HTTPS opsional. |
| Restore | Operator memilih snapshot dan target kosong berakhiran `_restore_test`; checksum dan query tabel diperiksa. Tidak ada `DROP`, `--clean`, atau overwrite produksi. |

RPO awal sekitar 24 jam saat backup sukses. RTO harus diukur lewat restore dengan
data nyata. Belum ada WAL/PITR. Filesystem upload tidak ikut kecuali mount dan
`STUDYHUB_BACKUP_UPLOADS_DIR` dikonfigurasi; snapshot file dan DB tidak atomik.
Gunakan versioning/object storage dan desain konsistensi upload bila fitur itu ditambahkan.

## Menyiapkan VPS sekali saja

Contoh mengasumsikan Linux dengan systemd, Docker Compose >=2.24.4, checkout di
`/opt/studyhub`, database bernama `studyhub`. Ganti path/host sesuai instalasi.
Jalankan administrasi host sebagai akun dengan izin yang sesuai.

1. Siapkan direktori privat `/etc/studyhub/secrets` (0700), file secret (0600),
   dan `/var/lib/studyhub-backup` (0700). Jangan simpan password di command history.
2. Buat role backup memakai [`create-backup-role.sql`](../ops/backup/create-backup-role.sql)
   sebagai admin PostgreSQL; sesuaikan pemilik migrasi `studyhub` jika berbeda.
   Atur password lewat `psql` interaktif: `\password studyhub_backup`.
3. Simpan URL koneksi di `/etc/studyhub/secrets/backup_database_url`:

   ```text
   postgresql://studyhub_backup:PASSWORD_YANG_DI_URL_ENCODE@postgres:5432/studyhub?sslmode=disable
   ```

   `disable` hanya contoh jaringan Docker privat pada satu host. Untuk database
   eksternal gunakan TLS `verify-full` dan CA terpercaya. Runner default memilih
   `verify-full`; bila memakai CA khusus, mount CA dan set `PGSSLROOTCERT`.
   Role backup harus SELECT semua tabel dan BYPASSRLS agar semua sekolah masuk.
   Role runtime API tidak boleh memiliki BYPASSRLS.
4. Buat password Restic acak yang panjang dan simpan sebagai `restic_password`.
   `uv run python scripts/init_ops.py` dapat membuat password/token lokal tanpa
   menampilkan nilainya; pindahkan dengan aman ke direktori VPS tadi.
   Simpan salinan password Restic **di luar VPS** dalam password/secret manager.
   Tanpa password tersebut backup terenkripsi tidak dapat dipulihkan.
5. Isi `/etc/studyhub/secrets/restic_repository`, misalnya:

   ```text
   s3:https://s3.example.net/bucket-sekolah/studyhub
   ```

   Buat bucket dan credential dengan hak minimum pada prefix ini. Restic perlu
   list/read/write/delete untuk retensi/prune. Aktifkan versioning dan evaluasi
   perlindungan penghapusan di provider; credential VPS yang dicuri juga dapat
   dipakai menghapus backup jika tidak ada perlindungan terpisah.
6. Salin [`backup.env.example`](../ops/backup/backup.env.example) ke
   `/etc/studyhub/backup.env` (0600), isi AWS/S3 credential jika diperlukan.
   Salin [`operations.env.example`](../ops/backup/operations.env.example) ke
   `/etc/studyhub/operations.env` (0600). Tetapkan host dan tag tetap; perubahan
   host/tag memisahkan kelompok retensi.

`app.env` adalah konfigurasi privat aplikasi yang dijelaskan pada
[runbook VPS](OPERASIONAL_VPS.md). Untuk command manual, export path backup
berikut ke shell administrasi:

```bash
cd /opt/studyhub
set -a
. /etc/studyhub/operations.env
set +a
docker compose --env-file /etc/studyhub/app.env -f compose.yaml -f compose.backup.yaml build backup
docker compose --env-file /etc/studyhub/app.env -f compose.yaml -f compose.backup.yaml run --rm backup init
docker compose --env-file /etc/studyhub/app.env -f compose.yaml -f compose.backup.yaml run --rm backup backup
```

`init` dijalankan sekali pada repository baru; perintah backup tidak membuat
repository baru diam-diam jika konfigurasi salah. File status ada pada
`/var/lib/studyhub-backup/status.json`. Pastikan snapshot terkonfirmasi, bukan
hanya container selesai dibuat. Jangan melanjutkan sebelum backup pertama sukses.

## Membuktikan pemulihan

Siapkan **database baru yang kosong** `studyhub_restore_test` melalui admin
PostgreSQL, sebaiknya pada instance yang terisolasi. Simpan DSN admin restore di
`/etc/studyhub/secrets/restore_database_url`. Role restore harus dapat membuat
objek/policy dan memeriksa semua tenant, misalnya admin pada instance uji.
Nama target wajib berakhiran `_restore_test`; jangan menunjuk database produksi.

```bash
docker compose --env-file /etc/studyhub/app.env -f compose.yaml -f compose.backup.yaml run --rm \
  -v /etc/studyhub/secrets/restore_database_url:/run/secrets/restore_database_url:ro \
  backup verify --snapshot latest --target-secret /run/secrets/restore_database_url
```

Runner mengunduh snapshot, memverifikasi checksum, lalu restore satu transaksi.
Tabel sekolah, user, subject dan versi migrasi diperiksa. Data target tetap ada
untuk pemeriksaan manual; perintah kedua akan ditolak karena target sudah terisi.
Gunakan target kosong baru untuk latihan berikutnya. Tetapkan jadwal latihan
restore minimal bulanan dengan penanggung jawab, catatan snapshot, durasi dan hasil.
Latihan restore produksi tidak dijadwalkan otomatis karena memerlukan provisioning
dan pembersihan instance uji yang tepat; pengujian integrasi repository mengotomasikan
latihan tersebut pada database tes sementara.

Arsip memakai `--no-owner --no-acl`: role, password, dan grant cluster PostgreSQL
tidak disalin oleh `pg_dump`. Setelah bencana, buat ulang role runtime/migrasi/backup,
grant dan secret dari konfigurasi aman. Jangan menjalankan API menggunakan role
admin pemilik hasil restore. Revisi schema harus cocok dengan versi source code.

Ketika pemulihan benar-benar diperlukan: hentikan penulisan, pilih snapshot yang
baik, pulihkan ke instance terisolasi, periksa data **semua sekolah**, buat ulang
role/grant, uji API/worker, lalu lakukan cutover terencana dengan administrator.
Jangan menyalakan worker hasil restore sebelum meninjau pending job/outbox:
event/task yang pernah terkirim setelah snapshot dapat dikirim ulang.
Pertimbangkan pencabutan session dan rotasi secret sesuai penyebab insiden.

## Mengaktifkan penjadwalan setelah backup dan restore lulus

```bash
sudo install -m 644 ops/systemd/studyhub-backup@.service /etc/systemd/system/
sudo install -m 644 ops/systemd/studyhub-backup.timer /etc/systemd/system/
sudo install -m 644 ops/systemd/studyhub-backup-maintenance.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now studyhub-backup.timer studyhub-backup-maintenance.timer
systemctl list-timers 'studyhub-*'
journalctl -u studyhub-backup@backup.service -n 50 --no-pager
```

Timer harus diaktifkan pada host sebenarnya; commit unit tidak mengaktifkannya.
Contoh ini memakai path `/opt/studyhub` dan `/usr/bin/docker` dari PATH systemd.
Sesuaikan unit bila instalasi berbeda. Local lock mencegah overlap pada satu host;
jangan menjalankan beberapa host dengan state directory berbeda dan tag yang sama
tanpa koordinasi. Restic juga memiliki locking repository tersendiri.

## Upload, webhook, dan batas perlindungan

Untuk upload lokal, tambahkan bind mount **read only** ke service backup melalui
override Compose privat, lalu set `STUDYHUB_BACKUP_UPLOADS_DIR` ke path container.
Untuk webhook, mount file URL HTTPS dan set `STUDYHUB_OPS_WEBHOOK_URL_FILE`.
Payload generik `{"text":"..."}` perlu receiver yang mendukung bentuk tersebut;
tidak semua webhook provider kompatibel. URL/token webhook adalah secret.

Staging dump sementara belum terenkripsi sebelum masuk Restic; direktori 0700 dan
umask 077 membatasi akses, kemudian staging dihapus. Gunakan enkripsi disk host
jika ancaman akses fisik relevan. Penghapusan file bukan secure erase.
Source code tersimpan di GitHub; environment, kredensial, CA dan password Restic
harus disimpan aman secara terpisah. Jangan memasukkannya ke repository kode.

Pemeriksaan mingguan membaca sampel, bukan seluruh data; lakukan `restic check
--read-data` berkala dari alat Restic dengan konfigurasi repository yang sama.
Tidak ada failover otomatis, backup Redis independen, maupun jaminan pemulihan
layanan pihak ketiga dalam implementasi ini. Setelah restore, cache boleh dibangun
ulang; antrean Redis bukan sumber kebenaran bisnis, job PostgreSQL direkonsiliasi.
