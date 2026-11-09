# Dasar integrasi pihak ketiga

API memakai `/api/v1`, JSON schema/OpenAPI, bearer authentication, UUID, pagination
terbatas dan idempotency key untuk job. Ini kontrak awal untuk frontend/mobile atau
adapter pihak ketiga. SSO OIDC tersedia sebagai login interaktif; API key service-to-service belum diaktifkan.
Lihat [multi-sekolah/SSO](multischool-sso.md) untuk pemisahan autentikasi dan membership.

`infrastructure/integrations.py` menyediakan adapter outbound dengan httpx pooled
client dari lifespan. Konfigurasi opsional:

```dotenv
STUDYHUB_INTEGRATION_BASE_URL=https://partner.example.org/api/
STUDYHUB_INTEGRATION_TOKEN=from-secret-manager
```

Token hanya diterapkan ke client khusus base URL tersebut; pemanggil tidak boleh
mengganti URL dari input HTTP. Tambahkan metode spesifik di adapter, model respons
Pydantic, klasifikasi error, retry ber-backoff hanya pada operasi idempoten, serta
idempotency key pada POST yang didukung partner. Jangan kirim data siswa tanpa
otorisasi dan minimisasi data.

Untuk webhook kelak: verifikasi signature pada raw body + timestamp, simpan event
ID unik untuk dedup dalam transaksi, catat job/outbox, balas cepat, proses di Celery.
Tidak ada webhook tanpa signature yang sengaja dibiarkan terbuka di fondasi ini.

Event internal versi 1 berbentuk:

```json
{"version":1,"event_id":"uuid","user_id":"uuid","school_id":"uuid","type":"job.completed","data":{"job_id":"uuid"}}
```

Event privat memiliki user_id eksplisit. Pengiriman bisa duplikat dan Pub/Sub
bisa kehilangan pesan ketika subscriber offline. Gunakan HTTP untuk rekonsiliasi;
jika partner membutuhkan replay/ack permanen, gunakan outbox delivery tersendiri
atau Redis Streams/message broker durable dengan consumer group.
