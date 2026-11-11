# Siswa dan pelaksanaan ujian — v0.5.0

Backend sekarang mendukung penempatan siswa per semester, kenaikan kelas,
tinggal kelas, kelulusan, daftar peserta ujian, pengerjaan dan penyimpanan jawaban,
penilaian dasar, serta pengumuman nilai. Alur ini memakai modul terbit dari
[panduan kolaborasi guru](KOLABORASI_UJIAN.md).

Semua endpoint di bawah berada di `/api/v1`, memakai bearer access token dan
`X-School-ID`. Swagger tersedia di `/docs` pada development. UUID dalam contoh
adalah placeholder yang diganti dengan ID dari respons API.

## Identitas siswa dan riwayat kelas

`User` tetap merupakan akun login global. `StudentProfile` menyimpan identitas
administratif di sekolah: akun, NIS (`student_number`, opsional), tingkat kelulusan,
status dan tanggal kelulusan. NIS unik di dalam sekolah. Profil hanya dapat dibuat
untuk akun dengan membership siswa aktif di sekolah tersebut.

`StudentEnrollment` menyimpan satu penempatan pada satu semester dan kelas.
Database menjamin satu enrollment aktif per siswa/sekolah serta satu rekaman
per siswa/sekolah/semester. Riwayat kelas tidak ditimpa ketika siswa naik kelas.
Setiap transisi menutup rekaman asal dan, jika relevan, membuat rekaman baru.

| Operasi | Aturan |
|---|---|
| Penempatan awal | Admin, profil aktif, periode terbuka, kelas tidak melebihi tingkat kelulusan |
| `continued` | Semester 1 → semester 2, tahun ajaran dan tingkat kelas sama |
| `promoted` | Semester 2 → semester 1 tahun ajaran berikutnya, naik tepat satu tingkat |
| `repeated` | Semester 2 → semester 1 tahun ajaran berikutnya, tingkat tetap |
| `graduated` | Semester 2 pada tingkat kelulusan profil siswa |
| `withdrawn` | Keluar sekolah; tidak membuat enrollment baru |

SMP lulus kelas 9; SMA kelas 12. Pada SMK, admin memilih kelas 12 untuk program
tiga tahun atau kelas 13 untuk program empat tahun. Siswa dengan tingkat kelulusan
13 tidak bisa dinyatakan lulus pada kelas 12. Konfigurasi ini eksplisit pada profil;
belum diturunkan otomatis dari katalog program keahlian.

Kelulusan/kenaikan kelas adalah keputusan admin, tidak ditentukan otomatis oleh
nilai ujian. Tanggal efektif harus sudah terjadi menurut tanggal UTC server.
Selain keluar sekolah, semester asal harus sudah berakhir. Tanggal penutupan
asal tidak boleh melewati tanggal mulai periode tujuan. Periode asal boleh sudah
berstatus `closed`; tujuan harus `open`. Sesi ujian yang masih berjalan harus
selesai sebelum enrollment dapat ditransisikan.

Akun alumni tetap dapat login dan membaca riwayat/hasil sendiri selama membership
aktif. Status alumni mencegah enrollment dan sesi ujian baru. Menonaktifkan akun
atau membership tetap mencabut akses sesuai mekanisme autentikasi fondasi.

### Endpoint dan contoh

| Endpoint | Akses |
|---|---|
| `POST /students`, `GET /students` | Admin sekolah |
| `POST /student-enrollments`, `GET /student-enrollments?period_id=...` | Admin sekolah; daftar dapat difilter student_id/class_id |
| `POST /student-enrollments/{id}/transition` | Admin sekolah |
| `GET /student/profile`, `GET /student/enrollments` | Siswa/alumni membaca data sendiri |

Profil baru:

```json
{"user_id": "UUID_AKUN_SISWA", "student_number": "12345", "graduation_grade": 12}
```

Penempatan awal:

```json
{"student_id": "UUID_PROFIL", "period_id": "UUID_SEMESTER", "class_id": "UUID_KELAS"}
```

Kenaikan kelas melalui endpoint transisi enrollment asal:

```json
{
  "expected_revision": 1,
  "action": "promoted",
  "effective_on": "2026-06-30",
  "target_period_id": "UUID_SEMESTER_GANJIL_BERIKUTNYA",
  "target_class_id": "UUID_KELAS_NAIK"
}
```

Kelulusan:

```json
{"expected_revision": 1, "action": "graduated", "effective_on": "2026-06-30"}
```

Respons transisi berisi `previous`, `current` dan `student`. `current` bernilai null
pada kelulusan/keluar sekolah. HTTP 409 untuk konflik revisi/keadaan mencegah
permintaan bersamaan membuat dua kelas aktif. Riwayat enrollment yang sudah
berakhir dilindungi trigger database dari perubahan dan penghapusan.

## Menyiapkan ujian kelas

Pembuatan modul dan penerbitan versinya mengikuti alur v0.4. Setelah itu, guru
membuat `POST /class-exams` dengan ID versi paket, ID penugasan, jadwal dan
`duration_minutes` (1–360, default 60). Ujian kelas dibuat sebagai **draft**.
Mengubah draft memakai `PUT /class-exams/{id}/schedule`:

```json
{
  "expected_revision": 1,
  "starts_at": "2026-11-15T08:00:00+07:00",
  "ends_at": "2026-11-15T10:00:00+07:00",
  "duration_minutes": 90
}
```

Terbitkan melalui `POST /class-exams/{id}/publish`:

```json
{"expected_revision": 2}
```

Publikasi mengambil 1–1000 siswa dengan enrollment aktif yang cocok dengan
kelas/periode, profil aktif, dan akun/membership siswa aktif. Daftar peserta
menjadi snapshot tetap. Siswa yang baru masuk sesudah publikasi tidak otomatis
mendapat akses. Pada versi ini belum ada alur penambahan peserta setelah publikasi;
pastikan penempatan siswa lengkap sebelum menerbitkan.

Jadwal, durasi, paket dan daftar peserta terbit tidak dapat diubah. Guru dengan
penugasan aktif pada **mapel + kelas + periode yang sama**, termasuk pengajaran
bersama, dan admin sekolah dapat mengelola ujian. Jabatan koordinator saja tidak
memberikan hak melihat jawaban semua kelas. Periode tidak dapat ditutup selama
masih memiliki jadwal ujian terbit yang belum berakhir.

## Pengerjaan siswa

| Endpoint | Hasil |
|---|---|
| `GET /student/exams` | Daftar ujian yang siswanya tercatat sebagai peserta; metadata saja |
| `POST /student/exams/{id}/attempt` | Memulai atau melanjutkan satu sesi yang sama |
| `GET /student/attempts/{id}` | Soal aman, jawaban tersimpan, status dan waktu server |
| `PUT /student/attempts/{id}/answers` | Menyimpan batch jawaban; respons status/revisi ringkas |
| `POST /student/attempts/{id}/submit` | Mengumpulkan jawaban terakhir yang sudah tersimpan |

Satu peserta mempunyai tepat satu attempt. Dua perangkat yang memulai bersamaan
mendapat ID yang sama; tidak ada attempt tambahan. Memulai hanya diizinkan dalam
jendela jadwal dan saat profil/enrollment masih aktif.

Tenggat ditetapkan sekali:

```text
deadline_at = min(waktu mulai menurut PostgreSQL + durasi, ends_at)
```

`server_time` dan `deadline_at` digunakan client untuk countdown. Mengubah jam
perangkat tidak menambah waktu. Server memeriksa `clock_timestamp()` setelah
mendapat row lock, sehingga request yang masuk sebelum tenggat namun baru mendapat
lock sesudah tenggat tetap ditolak. Tenggat, identitas sesi dan jawaban yang sudah
dikumpulkan dilindungi trigger database.

Contoh autosave:

```json
{
  "expected_revision": 1,
  "answers": [{"question_id": "UUID_SOAL", "value": "A"}]
}
```

Maksimal 20 jawaban per request dan 8000 karakter per jawaban. Soal harus menjadi
bagian paket; pilihan ganda hanya menerima kunci opsi yang tersedia. Esai menerima
teks biasa. Nilai null/kosong menghapus jawaban sebelum pengumpulan. `expected_revision`
harus mengikuti respons terakhir. Pada 409, ambil keadaan terbaru dan rekonsiliasi
perubahan; jangan menimpa jawaban dari perangkat lain secara otomatis.

Autosave mengembalikan status, revisi, waktu dan hasil yang boleh dilihat, tanpa
mengirim ulang seluruh soal atau jawaban. Untuk memulihkan keadaan setelah reconnect,
gunakan GET attempt. Saat submit, kirim `expected_revision`; submit tidak menerima
jawaban baru. Pastikan autosave terakhir berhasil sebelum submit. Pengumpulan
berulang tidak menilai ulang atau membuat event penyelesaian ganda.

Siswa tidak menerima `correct_option`, rubrik penilaian, identitas penulis, ataupun
struktur penilaian internal. Serializer siswa menggunakan daftar field eksplisit.
Endpoint modul lengkap dan endpoint guru tetap menolak siswa. Kepemilikan attempt
diperiksa berdasarkan akun siswa selain batas sekolah; ID siswa lain tidak
memberikan akses baca, simpan atau submit.

## Penutupan otomatis dan Celery

Task `studyhub.expire_exam_attempts` dijadwalkan Celery Beat setiap 30 detik.
Task memproses sekolah secara bertahap dan maksimal 100 attempt kedaluwarsa per
sekolah per putaran. Worker memakai koneksi sinkron psycopg milik prosesnya dan
konteks RLS sekolah yang tepat. Ia tidak memakai AsyncSession API.

Worker mengunci attempt dengan `SKIP LOCKED`, menilai jawaban terakhir yang sudah
tersimpan, dan menulis event `exam.submitted` ke transactional outbox pada transaksi
yang sama. Pengiriman ulang task aman: attempt yang sudah selesai dilewati.
Event privat hanya memuat ID attempt, tanpa jawaban atau nilai. WebSocket yang
sudah ada dapat menerima event tersebut; GET attempt tetap sumber kebenaran saat
reconnect karena Redis Pub/Sub tidak menyimpan riwayat.

Batas waktu **tetap berlaku bila Celery/Redis terganggu**. API juga menutup sesi
kedaluwarsa ketika diakses, dan menolak perubahan setelah deadline. Celery
memastikan siswa yang putus koneksi dan tidak kembali tetap ditutup. Backlog worker
bisa menunda perubahan status tercatat melebihi 30 detik, tetapi tidak membuka
kesempatan mengirim jawaban terlambat. Retry koneksi gagal tidak menggantikan
jawaban yang belum pernah berhasil disimpan di server.

## Penilaian dan pengumuman

Pilihan ganda benar memperoleh poin penuh; salah/kosong memperoleh nol. Esai
kosong memperoleh nol; esai terisi menunggu guru. Skor memakai `Decimal` dan kolom
PostgreSQL `NUMERIC`, dengan ketelitian dua angka desimal untuk nilai manual.

| Endpoint | Fungsi |
|---|---|
| `GET /class-exams/{id}/participants` | Peserta dari roster terbit |
| `GET /class-exams/{id}/attempts` | Metadata sesi dan status penilaian; pagination |
| `GET /exam-attempts/{id}` | Jawaban dan penilaian internal, khusus guru berwenang/admin |
| `PUT /exam-attempts/{id}/essay-grades` | Memberikan/memperbaiki poin esai sebelum rilis hasil |
| `POST /class-exams/{id}/release-results` | Mengumumkan nilai kepada siswa |

Nilai esai:

```json
{
  "expected_revision": 4,
  "grades": [{"question_id": "UUID_SOAL_ESAI", "points": "3.50"}]
}
```

Poin harus 0 sampai bobot soal dan tidak boleh digunakan untuk mengganti penilaian
pilihan ganda. Status `submitted` berarti masih ada esai menunggu nilai; `graded`
berarti semua soal telah dinilai. Perubahan nilai menggunakan revisi dan audit.

Siswa menerima `result: null` sebelum pengumuman, termasuk bila pilihan gandanya
sudah dinilai otomatis. Guru merilis dengan `expected_revision` ujian kelas.
Semua attempt yang dimulai pada kelas itu harus berstatus graded dan semua jadwal
terbit yang menggunakan **versi paket yang sama** harus berakhir. Draft kelas yang
belum diterbitkan tidak menahan rilis; sesudah hasil versi paket dirilis, draft
tersebut tidak dapat diterbitkan memakai versi itu. Gunakan versi baru untuk ujian
berikutnya. Ini menghindari pengumuman nilai saat kelas lain masih mengerjakan
paket yang sama.

Sesudah rilis, nilai tidak bisa diedit melalui API ini. Siswa melihat skor total,
bobot total dan persentase; kunci/rubrik tetap tidak dikirim. Peserta yang tidak
pernah memulai tidak diberi attempt atau nilai nol secara otomatis. Guru dapat
membandingkan roster dengan daftar attempt untuk melihat ketidakhadiran.

Penilaian, penutupan sesi kedaluwarsa dan pengumuman hasil tetap dapat dilakukan
sesudah periode ditutup; penulisan soal/penugasan baru pada periode tertutup tetap
ditolak. Kenaikan kelas/kelulusan administratif juga dapat memproses periode asal
yang sudah ditutup.

## Struktur, migrasi dan pengoperasian

- `academics/student_models.py`, `student_schemas.py`, `enrollment_service.py`,
  `student_api.py`: profil, riwayat semester, aturan transisi dan API.
- `assessments/delivery_models.py`, `delivery_schemas.py`, `delivery_service.py`,
  `delivery_api.py`: roster, attempt, akses, autosave, deadline dan rilis hasil.
- `assessments/grading.py`: logika penilaian bersama API dan worker.
- `worker/exams.py`: reconciler deadline sinkron dengan transaksi pendek.

Semua tabel baru memakai RLS `FORCE`, composite foreign key antar data sekolah,
index untuk roster/deadline dan constraint keunikan. Urutan lock yang konsisten
(period → profil siswa bila diperlukan → ujian → peserta/attempt) melindungi
publikasi roster, transisi siswa, autosave, penilaian dan rilis hasil. Status dan
otorisasi dibaca dari PostgreSQL; Redis tidak menyimpan sumber kebenaran jawaban.

Migrasi `a51d8e70bc92` setelah `f47b2a91cd30` mempertahankan ujian kelas lama,
mengisi period_id dari penugasan, dan memberi status draft serta durasi awal
60 menit. Profil/enrollment siswa harus dimasukkan eksplisit sebelum publikasi.
Tidak ada migrasi otomatis dari MongoDB. Backup PostgreSQL yang sudah tersedia
mencakup tabel baru secara otomatis.

```bash
uv sync --frozen
uv run alembic upgrade head
uv run python -m studyhub.provision  # khusus development
```

Restart API, worker **dan Beat** memakai versi yang sama untuk mengaktifkan task
baru. Compose tetap memakai service migrate sebelum API/worker. Produksi memakai
role migrasi terpisah dan grant tabel baru ke role runtime sebagaimana panduan
kolaborasi; jangan memberi SUPERUSER/BYPASSRLS pada runtime. Downgrade menolak
menghapus data siswa/attempt atau konfigurasi ujian baru yang telah digunakan.

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest tests/test_delivery_validation.py -q
STUDYHUB_RUN_INTEGRATION=1 uv run pytest tests/test_student_delivery.py -q
# Suite lengkap memerlukan Restic dan Docker sesuai README.
STUDYHUB_RUN_INTEGRATION=1 uv run pytest -q
```

Pengujian memakai PostgreSQL nyata, runtime tanpa bypass RLS, dua API, worker
Celery, dan Redis. Cakupannya termasuk migrasi data lama, dua perangkat menyimpan
bersamaan, request menunggu lock sampai melewati deadline, siswa putus koneksi,
guru pengajar bersama, kunci/nilai tersembunyi, kepemilikan jawaban, riwayat kelas,
kelulusan program SMK tiga/empat tahun, dan transisi bersamaan.

Rilis ini menyediakan API backend. UI ujian, remedial/retake, mutasi kelas di tengah
semester, pemulihan keputusan kelulusan, penambahan peserta setelah publikasi,
pengacakan soal, proctoring, ekspor rapor dan penilaian otomatis berbasis AI belum
tersedia. Pengujian fungsional/concurrency ini bukan benchmark kapasitas produksi.
