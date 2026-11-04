# Studio: rencana fitur project, posting & views (self-host)

Status: Fase 0–3 selesai (Fase 3: YouTube diprioritaskan) · Target: mode self-host (`BILLING_ENABLED` tidak aktif)

## 1. Tujuan

Mengubah OpenShorts dari "generator clip sekali pakai" menjadi workspace kecil:

1. Project dan clip tersimpan permanen dan bisa dibuka lagi.
2. Info project, clip, campaign, posting dan views disimpan di SQLite.
3. Clip bisa dipublish (otomatis lewat adapter, atau manual dengan menempel URL).
4. Daftar postingan per akun/platform.
5. Views dicatat harian sebagai time-series, lalu dihitung estimasi payout per campaign.

Non-goal (untuk sekarang): multi-user, billing, sinkronisasi dengan mode Cloud.

## 2. Kondisi repo saat ini (hasil audit)

| Area | Yang sudah ada | Gap untuk self-host |
|---|---|---|
| Output job | `output/{job_id}/` berisi clip + `*_metadata.json` per clip; `_recover_jobs_from_disk()` membaca ulang saat restart | Dihapus `cleanup_jobs()` setelah `JOB_RETENTION_SECONDS` (default 86400 = 24 jam di self-host), plus `_enforce_output_size_cap()` |
| Project & history | `cloud/videos.py` punya `/api/projects`, `/api/history`; `HistoryTab.jsx` memakainya; `/api/projects/{job_id}/restore` di `app.py` | Semuanya hanya untuk Cloud (Postgres + R2, butuh login). Self-host tidak punya library sama sekali |
| Database | `alembic/` + `cloud/models.py`, `sqlalchemy` ada di `requirements-billing.txt` | Tidak ada DB di self-host; `sqlalchemy` tidak ada di `requirements.txt` |
| Posting | `/api/social/post`, `/api/social/scheduled` lewat Upload-Post | Hanya Upload-Post; tidak ada catatan lokal "clip X diposting ke akun Y dengan URL Z" |
| Views | `/api/social/analytics*` + `social_metrics.py` membaca history Upload-Post | Hanya postingan via Upload-Post, live read dibatasi (`MAX_LIVE_READS`), tidak ada riwayat harian |

Catatan lisensi: kode di `cloud/` berlisensi komersial. Studio **tidak boleh import atau menyalin** kode dari `cloud/`. Cukup meniru konsepnya.

## 3. Prinsip arsitektur

1. **Semua di package baru `studio/`**. `app.py` hanya disentuh di 2-3 titik (include router, start scheduler di lifespan, opsional hook auto-save). Ini supaya `git merge upstream/main` tetap mudah.
2. **Aktif hanya di self-host**: router di-include kalau `not BILLING_ENABLED` dan `STUDIO_ENABLED` (default `true`).
3. **File project terpisah dari folder job**: `STUDIO_DIR` (default `studio_data/`) berisi `studio.db` dan `projects/{project_id}/`. Folder ini tidak pernah disentuh `cleanup_jobs()`, jadi tidak perlu mengubah logika retensi upstream.
4. **SQLite + SQLAlchemy 2 (sync)**, `PRAGMA journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout=5000`. Endpoint ditulis sebagai `def` (bukan `async def`) supaya FastAPI menjalankannya di threadpool.
5. **Migrasi sederhana**: tabel `schema_version` + daftar fungsi migrasi berurutan di `studio/migrations.py`. Tidak memakai `alembic/` milik Cloud supaya tidak bentrok.
6. **Adapter untuk dunia luar**: `PublisherAdapter` (publish) dan `MetricsFetcher` (views) punya interface yang sama untuk Upload-Post, API resmi, yt-dlp, dan manual.
7. Semua request keluar (yt-dlp, scrape) lewat `ssrf_guard.guarded()`, sesuai aturan repo.

### Struktur folder

```
studio/
  __init__.py
  config.py          # STUDIO_ENABLED, STUDIO_DIR, STUDIO_TZ, SNAPSHOT_AT
  db.py              # engine, session, PRAGMA, init + migrate
  migrations.py
  models.py          # tabel di bagian 4 (metadata sendiri, bukan cloud.models)
  schemas.py         # Pydantic request/response
  storage.py         # salin clip dari output/{job_id} ke projects/{id}, hash file
  router.py          # APIRouter prefix /api/studio
  services/
    projects.py
    review.py
    campaigns.py     # rumus payout
    posts.py         # dedupe, parse URL -> platform + post_id
  publishers/
    base.py          # PublisherAdapter
    manual.py
    uploadpost.py    # bungkus /api/social/post yang sudah ada
    youtube.py       # fase 4
  metrics/
    base.py          # MetricsFetcher
    ytdlp.py         # view_count/like_count dari metadata publik
    uploadpost.py    # pakai social_metrics.py
    youtube_api.py   # videos.list (murah kuota), fase 4
  scheduler.py       # loop harian snapshot views
dashboard/src/studio/
  StudioTab.jsx      # tab utama, sub-tab di bawah
  ProjectsPage.jsx
  ReviewQueue.jsx
  PostsPage.jsx
  CampaignsPage.jsx
  ChannelsPage.jsx
  api.js
tests/
  test_studio_db.py
  test_studio_projects.py
  test_studio_posts.py
  test_studio_metrics.py
  test_studio_payout.py
```

### Konfigurasi (env)

| Variabel | Default | Fungsi |
|---|---|---|
| `STUDIO_ENABLED` | `1` | Matikan Studio di self-host dengan `0` (di Cloud selalu mati) |
| `STUDIO_DIR` | `studio_data` | Lokasi `studio.db` dan `projects/` |
| `STUDIO_LINK_MODE` | `link` | `link` = hardlink file media bila satu filesystem (hemat disk), `copy` = selalu salin |
| `YOUTUBE_DATA_API_KEY` | kosong | API key YouTube Data API v3. Bila diisi, views YouTube dibaca lewat `videos.list` (50 video per panggilan, 1 unit kuota). Bila kosong, pakai yt-dlp |
| `STUDIO_SNAPSHOTS` | `1` | `0` mematikan pembacaan views harian otomatis (refresh manual tetap bisa) |
| `STUDIO_SNAPSHOT_AT` | `23:50` | Jam pembacaan harian (HH:MM) |
| `STUDIO_TZ` | `Asia/Jakarta` | Zona waktu jadwal; nama IANA atau offset seperti `+07:00`. Tanpa tzdata jatuh ke UTC+7 |
| `STUDIO_TRACK_DAYS` | `90` | Post lebih tua dari ini tidak dibaca lagi otomatis |

Hardlink hanya jalan bila `studio_data/` dan `output/` ada di mount yang sama. Linux menolak hardlink antar mount point (EXDEV), termasuk dua bind mount dari disk yang sama. Di `docker-compose.yml` bawaan keduanya mount terpisah, jadi Studio otomatis **menyalin** file. Hasilnya tetap benar, hanya pakai disk dua kali selama job asli belum dihapus cleanup.

## 4. Skema SQLite

Semua `id` = TEXT uuid4, waktu = TEXT ISO-8601 UTC.

```sql
channels (            -- akun/profil sosial milik sendiri
  id, name, platform,            -- youtube | tiktok | instagram | threads | facebook | x
  handle, language,              -- 'id' | 'en' ...
  caption_preset JSON,           -- gaya subtitle, font, posisi
  watermark_text,
  publisher,                     -- manual | uploadpost | youtube_api
  publisher_config JSON,         -- mis. profile Upload-Post (tanpa secret)
  created_at, archived_at
)

campaigns (
  id, name, platform_source,     -- nama program/clipping platform
  brief_url, cpm NUMERIC,        -- bayaran per 1000 views
  currency, min_views INTEGER,   -- minimal views untuk bisa klaim
  max_payout NUMERIC NULL,
  starts_at, ends_at,
  donation_pct NUMERIC DEFAULT 0,
  status,                        -- active | ended | archived
  notes, created_at
)

projects (
  id, title, source_url, source_type,   -- youtube | upload
  source_title, source_duration,
  job_id,                        -- job asal di output/
  campaign_id NULL REFERENCES campaigns,
  rights,                        -- campaign | permission | cc | own | unknown
  rights_note,
  status,                        -- active | archived
  created_at, updated_at
)

clips (
  id, project_id REFERENCES projects ON DELETE CASCADE,
  clip_index, file_path,         -- relatif terhadap STUDIO_DIR
  thumb_path, duration, start_s, end_s,
  title, hook_text, caption, hashtags,
  language,
  file_sha256,                   -- untuk deteksi duplikat
  review_status,                 -- draft | approved | rejected | posted
  reviewed_at, metadata JSON,    -- isi *_metadata.json asli
  created_at, updated_at
)

posts (
  id, clip_id REFERENCES clips,
  channel_id REFERENCES channels,
  campaign_id NULL REFERENCES campaigns,
  platform, external_id,         -- id video di platform
  url, posted_at,
  method,                        -- manual | uploadpost | youtube_api
  publish_request_id,            -- request_id Upload-Post bila ada
  status,                        -- pending | live | failed | removed
  submitted_to_campaign_at,      -- kapan disubmit ke campaign
  claim_status,                  -- none | submitted | approved | paid | rejected
  error, created_at,
  UNIQUE(platform, external_id)
)

view_snapshots (
  id, post_id REFERENCES posts ON DELETE CASCADE,
  captured_at, views, likes, comments, shares,
  source,                        -- ytdlp | uploadpost | youtube_api | manual
  UNIQUE(post_id, captured_at)
)

settings (key PRIMARY KEY, value JSON)
schema_version (version INTEGER)
```

Index: `clips(project_id)`, `clips(review_status)`, `posts(channel_id, posted_at)`, `posts(campaign_id)`, `view_snapshots(post_id, captured_at DESC)`.

Rumus payout (di `services/campaigns.py`, bukan di SQL):

```
eligible_views = views_terakhir if views_terakhir >= min_views else 0
payout         = min(eligible_views / 1000 * cpm, max_payout or ∞)
donasi         = payout * donation_pct / 100
```

## 5. API (`/api/studio`)

| Method | Path | Fungsi |
|---|---|---|
| GET | `/health` | DB OK, versi skema |
| POST | `/projects` | `{job_id, title?, campaign_id?, rights?}` → salin clip ke `projects/{id}/` |
| GET | `/projects` | list + filter `q`, `campaign_id`, `status`, paginasi |
| GET | `/projects/{id}` | detail + clips + ringkasan posting/views |
| PATCH | `/projects/{id}` | judul, campaign, rights, archive |
| DELETE | `/projects/{id}` | hapus row + folder (konfirmasi di UI) |
| POST | `/projects/{id}/reopen` | salin balik ke `output/{job_id}` supaya editor bawaan bisa dipakai |
| GET | `/clips` | review queue, filter `review_status` |
| PATCH | `/clips/{id}` | edit title/hook/caption, ubah status |
| GET | `/media/{path}` | serve file dari `STUDIO_DIR` (path guard seperti `test_media_path_guard`) |
| GET/POST/PATCH | `/channels` | archive lewat `PATCH {archived: true}` |
| CRUD | `/campaigns` | + `GET /campaigns/{id}/estimate?views=` |
| GET/PATCH | `/clips` | review queue; `force: true` melewati peringatan rights |
| GET | `/campaigns/{id}/report` | views, estimasi payout, donasi per post |
| POST | `/posts` | publish via adapter **atau** catat URL manual |
| GET | `/posts` | filter channel, platform, campaign, tanggal |
| PATCH | `/posts/{id}` | status, claim_status |
| POST | `/posts/{id}/snapshot` | ambil views sekarang (manual trigger) |
| GET | `/posts/{id}/snapshots` | time-series untuk grafik |
| POST | `/snapshots/run` | jalankan snapshot semua post aktif |
| GET | `/export/posts.csv` | export untuk rekap/klaim |

## 6. Fase pengerjaan

Estimasi untuk 1 developer, sudah termasuk test.

### Fase 0 — Persiapan (½ hari) ✅

- [ ] Tambah remote upstream di mesin lokal, lalu sync: `git fetch upstream && git merge upstream/main`.
- [x] Tambah `sqlalchemy==2.0.36` ke `requirements.txt` (pin sama dengan `requirements-billing.txt`).
- [x] Tambah volume `studio_data/` di `docker-compose.yml` dan `.gitignore`.
- [x] Kerangka `studio/` + `GET /api/studio/health`; include router di `app.py` (guard `STUDIO_ENABLED`).

**Selesai bila**: `pytest tests/ -v` hijau, `/api/studio/health` → `{"ok": true, "schema": 1}`.

### Fase 1 — Project tersimpan + SQLite (2–3 hari) ✅

- [x] `studio/db.py`: engine SQLite (WAL, FK, busy_timeout), `studio.init()` dipanggil di lifespan; migrasi di `studio/migrations.py`.
- [x] Model `projects`, `clips`, `settings` (`studio/models.py`).
- [x] `studio/projects.save_job()`: snapshot seluruh folder job ke `projects/{id}/files/` (media di-hardlink, metadata/subtitle disalin), dot-file dilewati, source video hanya bila `include_source`, `sha256` per clip. Simpan ulang = re-sync, status review tetap.
- [x] Endpoint project (save/list/by-job/detail/patch/delete/reopen) + `/api/studio/media` (Range didukung, path traversal ditolak).
- [x] Keputusan: **simpan hanya lewat tombol**, tanpa auto-save.
- [x] Dashboard: tab **Projects** (grid, search, active/archived, detail, putar, download, salin hook/caption, rename, rights, archive, delete, reopen). Tombol **save to projects** (+ opsi `+ source`) di samping *download all*.

**Selesai bila**: job selesai → disimpan → restart container dan tunggu > `JOB_RETENTION_SECONDS` → project dan clip masih bisa diputar dan di-reopen.

### Fase 2 — Review queue, campaign, channel, hak cipta (2–3 hari) ✅

- [x] Migrasi 2: tabel `channels`, `campaigns`; kolom `projects.campaign_id` (`ON DELETE SET NULL`) dan `clips.hashtags`. DB versi 1 di-upgrade tanpa kehilangan data.
- [x] Review queue (`studio/review.py`, `GET/PATCH /api/studio/clips`): filter status/campaign/project, edit title/hook/caption/hashtag, approve/reject/kembali ke draft. Shortcut **A** approve, **R** reject, **← →** pindah clip. `posted` tidak bisa diset manual (nanti diisi Fase 3).
- [x] Halaman Channels: nama, platform, handle, bahasa, watermark, preset caption (`default_hashtags`, `caption_suffix`), archive. Preset baru dipakai saat posting di Fase 3.
- [x] Halaman Campaigns: CPM, mata uang, min views, max payout per post, periode, % donasi, brief, catatan, status, statistik clip per status, dan kalkulator payout (`GET /campaigns/{id}/estimate`, rumus di `studio/catalog.estimate_payout`).
- [x] Approve clip dari project ber-`rights = unknown` dijawab 409 `rights_unknown`. UI minta konfirmasi, atau rights bisa langsung diset dari banner di review queue.
- [x] Project bisa dipasang ke campaign dari halaman detail project. Menghapus campaign hanya melepas link-nya, project tetap ada.

**Selesai bila**: alur draft → approved bisa dipakai penuh dari UI; campaign bisa dipasang ke project.

### Fase 3 — Postingan manual + snapshot views harian (3–4 hari) ✅

- [x] Migrasi 3: tabel `posts` (UNIQUE `platform, external_id`; `clip_id` SET NULL supaya riwayat payout tetap ada walau project dihapus) dan `view_snapshots`.
- [x] `studio/urls.py`: platform + `external_id` + URL kanonik untuk YouTube (shorts/watch/youtu.be/live), TikTok, Instagram, Threads, Facebook, X. Allowlist host; short link (`vm.tiktok.com`, `fb.watch`) ditolak, tidak di-follow.
- [x] **Record post** (dari review queue atau halaman Posts): pilih clip + channel, tempel URL. Clip jadi `posted`, campaign ikut project. URL yang sama ditolak (409); clip belum approved atau file yang sama di channel yang sama minta konfirmasi. Platform URL harus sama dengan platform channel. Hapus post → clip kembali `approved`.
- [x] `studio/metrics.py`: **YouTube Data API** (`YOUTUBE_DATA_API_KEY`, batch 50) sebagai sumber utama YouTube, yt-dlp sebagai cadangan dan untuk platform lain. Semua di `ssrf_guard.guarded()`, tanpa `PROXY_URL`, cookies dulu lalu anonim. Halaman tanpa view count = error, **tidak pernah dicatat sebagai 0**. Video hilang/privat → post `removed` dan berhenti dibaca.
- [x] `studio/scheduler.py`: sekali sehari jam `STUDIO_SNAPSHOT_AT` zona `STUDIO_TZ`, jeda acak 1,5–4 detik antar halaman yt-dlp, satu post gagal tidak menghentikan batch, run yang terlewat dijalankan saat start. `POST /snapshots/run` (background) dan `POST /posts/{id}/refresh`.
- [x] Input views manual (`source = manual`) bila platform tidak bisa dibaca.
- [x] Halaman **Posts**: filter channel/campaign/status, views terakhir, Δ 24 jam, estimasi payout, status klaim, grafik views per post (hover), refresh, hapus.
- [x] Laporan campaign (`GET /campaigns/{id}/report`): total views, payout, donasi di kartu campaign; export CSV (`GET /export/posts.csv`).
- [ ] Fetcher Upload-Post untuk post yang dipublish lewat Upload-Post → dipindah ke Fase 4 (baru relevan setelah publish otomatis ada).
- [ ] Verifikasi dengan URL nyata (dua malam berturut-turut) → perlu dijalankan di server kamu; sandbox pengembangan tidak bisa menjangkau YouTube.

**Selesai bila**: 3 URL nyata (YouTube, TikTok, Instagram) tercatat, snapshot otomatis masuk dua malam berturut-turut, grafik dan CSV benar.

### Fase 4 — Publish otomatis lewat adapter (3–5 hari, opsional)

- [ ] `PublisherAdapter.publish(clip, channel, caption) -> PublishResult(external_id?, url?, request_id?)`.
- [ ] `uploadpost.py`: membungkus alur `/api/social/post` yang sudah ada, lalu menulis row `posts` dan polling `post-status` untuk mengisi URL.
- [ ] `youtube.py` (opsional): OAuth desktop, `videos.insert` + `videos.list` untuk metrics. Token disimpan terenkripsi atau di file di luar repo, tidak pernah di DB polos.
- [ ] Jadwal posting per channel (pakai `/api/social/scheduled` bila lewat Upload-Post).

Catatan batasan platform (cek dokumentasi terbaru sebelum mulai): kuota YouTube Data API per upload besar; TikTok Content Posting API untuk app yang belum diaudit umumnya hanya bisa posting private; Instagram/Threads butuh akun Business/Creator + app review Meta. Karena itu adapter `manual` tetap jadi jalur utama.

### Fase 5 — Polish (berkelanjutan)

- [ ] Backup `studio.db` + `projects/` ke R2/S3 (pakai `s3_uploader.py`, endpoint R2), jadwal mingguan.
- [ ] Preset bahasa Indonesia: default model whisper, prompt hook & judul berbahasa Indonesia per channel.
- [ ] Dashboard ringkasan: views minggu ini, top clip, estimasi payout bulan berjalan.
- [ ] Tag/folder project, bulk action.

## 7. Titik sentuh di kode upstream

Dijaga sekecil mungkin supaya merge dari upstream tetap mulus:

| File | Perubahan |
|---|---|
| `app.py` | include `studio.router` (guarded); `studio.init_db()` + `studio.scheduler.start()` di `lifespan`; opsional 1 baris hook auto-save |
| `requirements.txt` | `sqlalchemy` |
| `docker-compose.yml` | volume `studio_data` |
| `dashboard/src/App.jsx` | 1 tab baru (lazy import `studio/StudioTab`) + tombol Save to Studio di hasil job |

## 8. Strategi test

- Test pakai SQLite di `tmp_path`, tanpa ML stack (sesuai CI).
- `test_studio_db`: migrasi idempoten, WAL aktif, FK aktif.
- `test_studio_projects`: simpan dari folder job palsu, restart (engine baru) → data tetap; path traversal ditolak di `/media`.
- `test_studio_posts`: parsing URL per platform, host di luar allowlist ditolak, deteksi duplikat.
- `test_studio_metrics`: fetcher di-mock; snapshot terlewat dijalankan saat start; error satu post tidak menghentikan batch.
- `test_studio_payout`: di bawah `min_views` = 0, `max_payout` dihormati, donasi benar.
- Sebelum push: `pytest tests/ -v`, `npm run build`, `npm run lint` di `dashboard/`.

## 9. Risiko & mitigasi

| Risiko | Mitigasi |
|---|---|
| Konflik merge dengan upstream yang sangat aktif | Kode di `studio/`, titik sentuh minimal, sync upstream rutin (mingguan) |
| yt-dlp gagal membaca views (IG/TikTok sering minta login atau rate limit) | Cookies opsional, jitter, retry, fallback ke Upload-Post atau input manual; status "degraded" bukan 0 views |
| Disk penuh karena project disalin | Hardlink bila memungkinkan, tampilkan ukuran per project, tombol hapus source, backup + prune ke R2 |
| SQLite terkunci saat worker konkuren | WAL + `busy_timeout`, transaksi pendek, tulis hanya dari proses API (bukan child process job) |
| Token/API key bocor | Secret hanya di env/file di luar repo; `publisher_config` tanpa secret |

## 10. Pertanyaan terbuka

1. Auto-save setiap job ke Studio, atau hanya lewat tombol?
2. Clip yang di-reject: tetap disimpan atau dihapus filenya?
3. Platform mana yang paling penting untuk views di Fase 3 (urutan prioritas)?
4. Perlu login sederhana (password tunggal) untuk dashboard bila diakses dari internet?
