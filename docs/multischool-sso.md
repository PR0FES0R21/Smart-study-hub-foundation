# Multi-sekolah, SMP/SMA/SMK dan SSO

## Identitas dan otorisasi

`users` adalah identitas global. `memberships(school_id,user_id)` menyimpan role
admin/teacher/student dan status aktif per sekolah. Satu orang dapat menjadi guru
di sekolah A dan admin di B. `is_platform_admin` mengizinkan provisioning
sekolah, pemetaan identitas SSO, dan penautan akun global; tidak membypass membership tenant.

Login/refresh menghasilkan session identitas global. Pilih sekolah melalui
`GET /api/v1/schools`, kemudian kirim **X-School-ID: UUID** pada endpoint akademik,
user list/create, job, serta permintaan ticket WebSocket. Header hanyalah selector:
server tetap memeriksa membership dan sekolah aktif pada setiap request.

Endpoint tambahan:

| Endpoint | Tujuan |
|---|---|
| `POST /api/v1/schools` | Platform admin membuat sekolah dan menjadi admin sekolah itu |
| `GET /api/v1/schools` | Sekolah aktif yang diikuti pengguna, beserta role |
| `GET /api/v1/schools/current` | Konteks, role dan capabilities jenjang |
| `POST /api/v1/schools/current/memberships` | Platform admin yang juga admin sekolah tujuan menautkan akun global |
| `POST, GET /api/v1/expertise-programs` | Program keahlian per sekolah; create hanya SMK |
| `POST, GET /api/v1/classes` | Contoh kelas dengan aturan grade dan program |

Membership admin tidak memberi hak platform admin. User create hanya menerima
teacher/student; penambahan admin sekolah dilakukan lewat endpoint membership.
Penautan user global memerlukan platform admin sekaligus membership admin sekolah
tujuan. Ini pembatasan sementara untuk melindungi profil antarsekolah sampai alur
invite/consent tersedia. Perubahan role, penghapusan membership, dan UI belum dibangun.
List user dibatasi membership sekolah; akun global dengan email sama tidak dibuat
ulang. Untuk user yang sudah ada, gunakan membership, bukan menimpa akun/password.

## Aturan jenjang

| Jenjang | Kelas yang didukung | Program keahlian |
|---|---|---|
| SMP | 7, 8, 9 | Tidak tersedia; ditolak bila diisi |
| SMA | 10, 11, 12 | Tidak tersedia; ditolak bila diisi |
| SMK | 10, 11, 12, 13 | Tersedia; kelas 13 mengakomodasi program empat tahun |

Program pada kelas SMK masih opsional dalam fondasi ini. Aturan kurikulum spesifik,
konsentrasi keahlian, tahun ajaran, dan syarat kelas tertentu dapat ditambahkan
sebagai domain tersendiri. Jenjang sekolah tidak disediakan sebagai field update
agar perubahan tidak merusak relasi kelas/program yang sudah terbentuk.

Aturan divalidasi dua lapis: service HTTP dan constraint PostgreSQL. Composite FK
program → `(schools.id,schools.level)` dengan CHECK `school_level='SMK'` menolak
program SMP/SMA bahkan lewat SQL langsung. Composite FK kelas → program memastikan
program sekolah lain tidak dapat direferensikan. Unique code/name dibatasi tenant.

## Isolasi data dan resource

Subjects, jobs, classes, dan expertise programs memiliki `school_id`, explicit
filter query, serta **ENABLE/FORCE RLS** dengan policy fail-closed. Context disetel
melalui `set_config(..., true)` dalam transaksi; hook Session memasangnya ulang
sesudah commit. Connection pool tidak menyimpan konteks tenant antar transaksi.
SSO/global identity tables dan outbox dispatcher bukan tabel tenant RLS: aksesnya
dilakukan lewat use case yang dibatasi. Jangan expose query umum untuk tabel itu.

Cache key dan generation memasukkan school UUID. Idempotency key job unik pada
`(school_id,owner_id,key)`. Outbox menyimpan school_id sebagai kolom otoritatif;
dispatcher mengirimnya ke task dan event. Worker menyalakan tenant context sebelum
membaca data. Reconciliation mengunjungi sekolah per transaksi, bukan bypass RLS.
Ticket WebSocket mengikat user, session dan sekolah; event privat harus cocok pada
keduanya. Membership dan session diperiksa ulang secara berkala.

API/worker tidak memerlukan SUPERUSER/BYPASSRLS. Development provisioning dibuat
oleh migrator terpisah; production startup menolak runtime role yang dapat bypass.
RLS merupakan pertahanan terhadap query yang lupa filter, bukan pengganti otorisasi
membership dan bukan isolasi dari aplikasi yang seluruh kredensial databasenya
sudah dikuasai penyerang. Opsi database-per-school dapat dievaluasi jika kebutuhan
isolasi institusi/regulasi lebih kuat daripada shared schema.

## SSO melalui OpenID Connect

Adapter mendukung authorization-code flow, PKCE S256, state sekali pakai dengan
binding cookie browser, nonce, issuer/audience/expiry, RS256 dan JWKS. Contoh
konfigurasi (gunakan credential IdP sungguhan melalui secret manager):

```dotenv
STUDYHUB_OIDC_ISSUER=https://identity.example.org/realms/studyhub
STUDYHUB_OIDC_CLIENT_ID=studyhub-web
STUDYHUB_OIDC_CLIENT_SECRET=from-secret-manager
STUDYHUB_OIDC_REDIRECT_URI=https://api.example.org/api/v1/auth/sso/callback
STUDYHUB_OIDC_ENDPOINT_ORIGINS=[]
```

Redirect URI harus didaftarkan persis di IdP. Discovery harus mengiklankan PKCE
S256; signing algorithm adapter saat ini hanya RS256. Endpoint discovery, token,
authorize dan JWKS memakai HTTPS dan origin issuer; jika provider memakai origin
lain, tambahkan origin HTTPS yang spesifik ke endpoint_origins. Client secret
memakai client_secret_basic; public client dapat memakai PKCE tanpa secret.
HTTP loopback hanya diizinkan pada environment=test untuk fixture protokol.

1. Provision user lokal dan membership. Platform admin menautkan `(issuer,subject)`
   ke user melalui `POST /api/v1/auth/sso/identities` dengan UUID user dan subject IdP.
   Subject harus diperoleh dari proses admin IdP yang dipercaya.
2. Browser menuju `GET /api/v1/auth/sso/start` dan mengikuti redirect IdP.
3. Callback memvalidasi state/cookie/PKCE/token lalu mengeluarkan session lokal.
   Fondasi mengembalikan tokens lewat JSON; frontend/BFF produksi dapat menggantinya
   dengan handoff sekali pakai dan penyimpanan refresh token yang sesuai desainnya.

Tidak ada auto-link berdasarkan email, auto-provision, atau kepercayaan pada
role/school_id dari token IdP. Credential provider tidak diteruskan ke client.
Callback code/state dan ticket disamarkan di log aplikasi/Uvicorn; ingress juga
wajib menghapus query credential dari log. Cookie secure/HttpOnly/SameSite=Lax;
satu flow SSO aktif per browser saat ini. Error IdP terkontrol, tidak membocorkan token.

Provider nyata belum tersambung karena issuer/client registration belum diberikan.
Tes memakai IdP lokal yang menandatangani JWT RSA dan memeriksa PKCE. Belum ada
back-channel logout, sinkronisasi penonaktifan akun IdP, atau global logout semua
aplikasi. Logout sekarang mencabut session aplikasi; session IdP terpisah. SSO
upstream yang dicabut tidak otomatis mencabut session lokal sampai expiry/revocation.

## Arah microservice

SSO memusatkan autentikasi di IdP; membership tetap otorisasi milik domain sekolah.
Pertahankan monolit modular sampai kebutuhan pemisahan jelas. Ketika service dipecah,
gunakan access token audience-specific dengan issuer/JWKS yang tepercaya dan kontrak
otorisasi tenant, service credentials, idempotensi serta event versi yang eksplisit.
JWT HS256 session lokal sekarang tidak untuk disebarkan dengan shared secret ke
semua microservice. Inbound ID token IdP tidak diterima sebagai access token API.
Tidak ada klaim bahwa pemisahan microservice selesai hanya karena OIDC tersedia.

## Upgrade dari v0.1

```bash
uv sync --frozen
uv run python scripts/upgrade_local_env.py  # hanya instalasi lama
uv run alembic upgrade head
uv run python -m studyhub.provision         # hanya development
```

Migrasi memindahkan data v0.1 yang ada ke sekolah **LEGACY** dengan level SMK dan
membership dari role lama. Pilihan ini placeholder migrasi: operator wajib memeriksa
nama/jenjang sebelum penggunaan nyata. Instalasi kosong tidak mendapat sekolah fiktif.
Admin lama menjadi platform admin dan admin legacy. Downgrade menolak data multi
sekolah/SSO/kelas/program yang akan hilang; gunakan strategi backup/restore eksplisit.
API v0.2 memerlukan X-School-ID, dan `/users/me` tidak lagi memiliki role global.
Hentikan/drain API dan worker v0.1 sebelum migrasi ini; perubahan schema dan argumen
task merupakan breaking release. Job pending yang kehilangan pesan lama dapat
dipublikasikan ulang oleh reconciliation setelah worker v0.2 aktif.

Production memakai migrator berizin DDL secara terpisah. Berikan runtime role hanya
CONNECT/USAGE dan DML yang diperlukan tanpa DDL, SUPERUSER, BYPASSRLS atau membership
ke role yang bisa bypass. Jangan kirim kredensial migrator ke container API/worker.
