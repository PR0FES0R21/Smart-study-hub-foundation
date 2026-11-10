# Kolaborasi guru dan modul ujian — v0.4.0

Rilis ini menambahkan alur operasional dari penugasan mengajar sampai penggunaan
paket soal terbit pada kelas. Semua endpoint memakai access token dan header
`X-School-ID`. Contoh dapat dicoba melalui `/docs` pada lingkungan pengembangan.

## Aturan bisnis

Kelompok koordinasi mempunyai identitas tetap dan cakupan **sekolah + periode
akademik + mapel + tingkat kelas**. Periode adalah satu semester dalam tahun ajaran;
`year_start=2026` berarti tahun ajaran 2026/2027. Setiap semester memiliki
penugasan sendiri; tidak ada perpindahan periode otomatis berdasarkan jam server.

Admin sekolah menunjuk satu guru aktif yang mempunyai penugasan mengajar dalam
cakupan tersebut. Penunjukan adalah tanggung jawab tambahan yang dibatasi cakupan,
bukan role global. Guru A boleh menjadi koordinator Matematika kelas 12 sekaligus
anggota biasa Bahasa Indonesia kelas 10. Satu guru boleh mengajar banyak mapel dan
kelas; beberapa guru boleh mengajar mapel yang sama pada satu kelas.

Kelas yang berbeda program keahlian bergabung bila mapel dan tingkat kelasnya sama.
SMP menggunakan tingkat 7–9, SMA 10–12, SMK 10–13. Program keahlian tetap khusus
SMK. Kelompok kurikulum/peminatan yang membutuhkan paket berbeda belum dimodelkan;
gunakan identitas mapel berbeda jika cakupan materi memang berbeda.

Kelas pada fondasi ini merupakan referensi rombel yang dapat dipakai dalam periode
berbeda. Penugasan mengajar selalu menyimpan periode. Riwayat penempatan siswa,
kenaikan kelas dan kelulusan belum menjadi bagian rilis ini; kelak harus memakai
rekaman enrollment per periode, sehingga tidak mengubah riwayat kelas lama.

## Alur API

| Langkah | Endpoint `/api/v1` | Pelaku |
|---|---|---|
| Buat/lihat semester | `POST /academic-periods`, `GET /academic-periods` | Admin membuat; guru/admin membaca |
| Tutup semester | `POST /academic-periods/{id}/close` | Admin |
| Buat/lihat tugas mengajar | `POST /teaching-assignments`, `GET /teaching-assignments?period_id=...` | Admin membuat; guru melihat miliknya |
| Aktifkan/nonaktifkan tugas | `PUT /teaching-assignments/{id}/status` | Admin |
| Buat kelompok dan tunjuk koordinator | `POST /coordination-groups` | Admin |
| Ganti/cabut koordinator | `PUT /coordination-groups/{id}/coordinator` | Admin |
| Kelompok yang dapat diakses | `GET /coordination-groups?period_id=...` | Guru terkait/admin |
| Detail kelompok/anggota | `GET /coordination-groups/{id}`, `GET /coordination-groups/{id}/teachers` | Guru terkait/admin |
| Buat/ubah soal | `POST /questions`, `PUT /questions/{id}` | Guru penulis |
| Daftar/detail soal | `GET /questions?group_id=...&status=submitted`, `GET /questions/{id}` | Penulis, koordinator cakupan, admin |
| Ajukan soal | `POST /questions/{id}/submit` | Penulis |
| Setujui/tolak | `POST /questions/{id}/review` | Koordinator yang sedang ditunjuk |
| Buat/ubah draft modul | `POST /exam-modules`, `PUT /exam-modules/{id}` | Koordinator |
| Daftar modul | `GET /exam-modules?group_id=...` | Guru terkait/admin |
| Detail draft | `GET /exam-modules/{id}` | Koordinator/admin |
| Terbitkan versi | `POST /exam-modules/{id}/publish` | Koordinator |
| Daftar versi | `GET /exam-modules/{id}/versions` | Guru terkait/admin; metadata saja |
| Ambil paket terbit | `GET /module-versions/{id}` | Guru terkait/admin |
| Gunakan paket untuk kelas | `POST /class-exams` | Guru sesuai tugas mengajarnya |
| Daftar ujian kelas | `GET /class-exams?period_id=...` | Guru sesuai tugas aktif/admin |
| Riwayat tindakan | `GET /academic-audit?resource_id=...` | Admin |

Semua daftar memakai `limit`/`offset` dengan batas server. Penunjukan dan modifikasi
soal/modul menggunakan `expected_revision`. Ambil nilai `revision` terbaru dari
respons sebelumnya. HTTP 409 berarti keadaan telah berubah atau operasi
bertentangan dengan aturan; muat ulang sebelum mengambil keputusan, jangan retry
buta dengan nomor revisi baru. HTTP 403 menandakan tidak berwenang, 404 objek tidak
ditemukan dalam sekolah yang dipilih, dan 422 data tidak valid.

### 1. Menyiapkan penugasan

Buat sekolah, akun guru, mapel dan kelas menggunakan endpoint fondasi sebelumnya.
Admin sekolah biasa dapat melakukan seluruh alur akademik ini; tidak membutuhkan
status platform admin. Login akun terkait melalui `POST /auth/login` untuk
mendapatkan access token, lalu isi **Authorize** pada Swagger.

`POST /academic-periods`:

```json
{
  "year_start": 2026,
  "semester": 1,
  "starts_on": "2026-07-01",
  "ends_on": "2026-12-31"
}
```

`POST /teaching-assignments`, diulang untuk setiap kombinasi guru/mapel/kelas:

```json
{
  "period_id": "UUID_PERIODE",
  "class_id": "UUID_KELAS_12_TKJ_2",
  "subject_id": "UUID_MATEMATIKA",
  "teacher_id": "UUID_GURU_A"
}
```

`POST /coordination-groups`:

```json
{
  "period_id": "UUID_PERIODE",
  "subject_id": "UUID_MATEMATIKA",
  "grade_level": 12,
  "coordinator_id": "UUID_GURU_A"
}
```

ID pada contoh adalah placeholder; ganti dengan UUID dari respons API. Setiap
referensi wajib berasal dari sekolah yang sama. Kelompok kosong koordinatornya
bisa tetap menyimpan soal dan modul; isi `coordinator_id: null` bersama
`expected_revision` untuk mencabut penunjukan.

### 2. Pengajuan dan review soal

Sebagai guru B yang mengajar Matematika kelas 12, `POST /questions`:

```json
{
  "group_id": "UUID_KELOMPOK",
  "content": {
    "kind": "multiple_choice",
    "prompt": "Berapakah 2 + 2?",
    "options": [{"key": "A", "text": "4"}, {"key": "B", "text": "5"}],
    "correct_option": "A"
  }
}
```

Pilihan ganda memerlukan 2–8 opsi unik dengan kunci A–H dan jawaban yang menunjuk
salah satu opsi. Soal esai memakai `kind: "essay"`, `prompt` dan `rubric`, tanpa
opsi/jawaban pilihan ganda. Konten disimpan sebagai teks biasa; frontend harus
merender dengan escaping, bukan menyisipkan HTML mentah.

Ajukan dengan `POST /questions/{id}/submit`:

```json
{"expected_revision": 1}
```

Koordinator mereview dengan `POST /questions/{id}/review`:

```json
{"expected_revision": 2, "decision": "approved", "note": "Sesuai cakupan materi"}
```

Status mengikuti `draft → submitted → approved/rejected`. Soal yang sedang
`submitted` terkunci untuk edit. Penolakan wajib memiliki catatan. Mengedit soal
yang sudah disetujui membuatnya kembali `draft` dan memerlukan review ulang.
Setiap perubahan status juga menaikkan revisi untuk menghindari penggunaan
keputusan lama. Penulis tetap tercatat. Audit mempertahankan aktor, keputusan dan
nomor revisi; catatan review pada soal adalah catatan terakhir. Snapshot isi soal
historis disimpan ketika diterbitkan dalam versi modul.

Koordinator boleh berkontribusi dan mereview soal sendiri. Pemisahan wajib antara
penulis dan reviewer (persetujuan dua orang) belum menjadi aturan produk ini.

### 3. Menyusun dan menerbitkan modul

Koordinator membuat modul melalui `POST /exam-modules`:

```json
{"group_id": "UUID_KELOMPOK", "title": "UAS Matematika Kelas 12"}
```

Susun dengan `PUT /exam-modules/{id}`; urutan `items` menjadi urutan paket:

```json
{
  "expected_revision": 1,
  "title": "UAS Matematika Kelas 12",
  "items": [{"question_id": "UUID_SOAL", "question_revision": 3, "points": 10}]
}
```

Maksimal 200 soal unik, masing-masing 1–1000 poin. Soal harus disetujui, berasal dari
kelompok yang sama dan mempunyai revisi yang cocok. Terbitkan menggunakan
`POST /exam-modules/{id}/publish` dengan `expected_revision` modul terkini.

Publikasi menyimpan judul, urutan, isi, kunci/rubrik, penulis, revisi soal dan bobot
sebagai snapshot JSONB. SHA-256 dari JSON kanonis mengidentifikasi isi paket;
checksum bukan tanda tangan digital. Trigger database menolak update/delete versi
terbit. Perubahan bank soal atau draft modul kemudian tidak memengaruhi versi itu.
Publikasi berikutnya menghasilkan ID versi baru. Guru memakai ID versi eksplisit;
API tidak otomatis menggantinya dengan versi "latest".

### 4. Memakai versi bersama pada kelas

`POST /class-exams`:

```json
{
  "module_version_id": "UUID_VERSI_TERBIT",
  "assignment_id": "UUID_PENUGASAN_GURU_PADA_KELAS",
  "starts_at": "2026-11-15T08:00:00+07:00",
  "ends_at": "2026-11-15T10:00:00+07:00"
}
```

Guru A dan B menggunakan **ID versi terbit yang sama** pada tugas mengajar mereka.
Isi, urutan dan bobot soal terjamin sama; jadwal disimpan per kelas. Untuk satu
versi paket, satu kelas hanya dapat memiliki satu rekaman ujian, termasuk bila
diajar beberapa guru. Pemilihan tugas mapel/tingkat/periode berbeda ditolak.
Jadwal wajib menyertakan zona waktu, disimpan sebagai timestamp berzona, dan batas
tanggal periode dihitung dalam UTC. Frontend menampilkan sesuai zona sekolah.

Paket versi berbeda merupakan publikasi berbeda. Belum ada kalender ujian sekolah
atau aturan yang memaksa seluruh kelas memilih satu publikasi tertentu. Modul
keputusan kalender tersebut dapat ditambahkan di atas referensi versi ini.

`class-exams` saat ini merupakan konfigurasi ujian: pengerjaan siswa, autosave
jawaban, timer server, pengacakan, penilaian, pengawas dan pengumuman hasil belum
tersedia. Endpoint paket terbit mengandung kunci jawaban dan hanya dapat diakses
guru terkait/admin; siswa ditolak pada seluruh endpoint kolaborasi ini.

## Struktur dan alasan teknis

| Komponen | Kegunaan |
|---|---|
| `academics/` | Model, DTO, API dan transaksi periode, tugas mengajar, koordinasi dan audit |
| `assessments/` | Model, DTO, API dan transaksi bank soal, review, modul, versi dan konfigurasi ujian |
| PostgreSQL + SQLAlchemy async | Sumber kebenaran dan transaksi atomik; I/O nonblocking pada FastAPI |
| Composite foreign key | Memastikan relasi periode/mapel/kelas/guru/kelompok berasal dari sekolah yang sama |
| Unique constraint | Mencegah tugas identik, koordinator ganda, versi ganda dan ujian kelas duplikat |
| RLS dengan `FORCE` | Menolak pembacaan/penulisan lintas sekolah pada seluruh tabel baru |
| Row lock + `expected_revision` | Menjaga pergantian penugasan, review dan publikasi saat permintaan bersamaan |
| Snapshot JSONB | Menyimpan dokumen paket terbit yang memiliki bentuk validasi Pydantic |
| Audit append-only | Tindakan berhasil dicatat dalam transaksi yang sama; tidak menyimpan isi soal/kunci |
| Redis/Celery | Infrastruktur yang sudah ada; keputusan izin/publikasi baru dibaca langsung dari PostgreSQL |

Urutan lock: periode → kelompok → soal/modul. Perubahan tugas/koordinator dan
penutupan periode memperoleh lock eksklusif singkat pada periode. Pengeditan soal
menggunakan shared lock periode/kelompok sehingga penulis berbeda dapat bekerja
bersamaan. Koordinator menggunakan lock eksklusif kelompok saat menyusun/review;
publikasi membaca maksimal 200 soal dalam satu query dan mengambil lock dalam
urutan ID konsisten. Tidak ada HTTP eksternal atau pekerjaan Celery di dalam lock.

Strategi ini sengaja mengutamakan konsistensi konfigurasi sekolah. Perubahan
penugasan massal pada satu periode dapat saling menunggu; ukur kebutuhan sebelum
mengganti dengan lock yang lebih granular. Ekspor PDF/import massal nantinya cocok
menjadi tugas Celery, bukan bagian transaksi publikasi. Kunci jawaban tidak
dikirim melalui Pub/Sub dan tidak masuk cache bersama/log aplikasi.

Pergantian koordinator mempertahankan ID kelompok dan seluruh modul. Hak kelola
mengikuti koordinator aktif, bukan pembuat modul terdahulu. Sebelum menonaktifkan
tugas terakhir seorang koordinator dalam kelompok, admin harus mengganti/mencabut
penunjukannya. Menonaktifkan membership/akun langsung mencabut akses; admin tetap
dapat menunjuk pengganti. Menutup periode menghentikan seluruh penulisan akademik
periode itu dan mempertahankan akses baca yang berwenang.

## Upgrade, operasi dan pengujian

Migrasi baru: `f47b2a91cd30`, setelah `e21aa6c09f52`. Tidak memigrasikan dokumen
MongoDB lama dan tidak mengubah kelas/mapel yang sudah ada. Penugasan baru dibuat
secara eksplisit. Backup PostgreSQL yang sudah tersedia akan mencakup tabel baru.

Untuk pengembangan lokal:

```bash
uv sync --frozen
uv run alembic upgrade head
uv run python -m studyhub.provision
# Restart API setelah migrasi; worker dapat mengikuti deployment aplikasi.
```

Untuk Compose, jalankan deployment/rebuild normal: service `migrate` melakukan
migrasi dan provisioning sebelum API berjalan. Pada produksi, gunakan role migrasi
terpisah dan berikan privilege tabel baru kepada role runtime melalui release
process. Jika belum ada default privileges, operator database menjalankan `GRANT
SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO <runtime_role>`
dengan identifier role yang benar; RLS dan trigger tetap berlaku. Role runtime
harus tanpa SUPERUSER/BYPASSRLS. `studyhub.provision` khusus development.

Downgrade menolak bila salah satu tabel akademik baru sudah berisi data, agar
rollback tidak menghapus pekerjaan guru. Ekspor/backup dan rencana pemulihan perlu
dilakukan sebelum rollback skema yang sudah dipakai. Trigger juga menjaga versi
terbit dan audit dari perubahan langsung melalui koneksi runtime; operator dengan
privilege DDL tetap harus dilindungi secara operasional.

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest tests/test_collaboration_validation.py -q
STUDYHUB_RUN_INTEGRATION=1 uv run pytest tests/test_academic_collaboration.py -q
# Seluruh suite termasuk backup membutuhkan Restic dan Docker seperti README.
STUDYHUB_RUN_INTEGRATION=1 uv run pytest -q
```

Tes integrasi menggunakan PostgreSQL nyata, role runtime tanpa bypass RLS, dua
proses API, Redis dan worker yang disiapkan fixture. Kasus mencakup kolaborasi
lintas program, akses antar sekolah/mapel, privasi bank soal, pergantian koordinator,
konflik penyimpanan/publikasi bersamaan, snapshot yang stabil, periode tertutup,
validasi SMP/SMA/SMK, serta penolakan perubahan langsung pada audit/versi terbit.
