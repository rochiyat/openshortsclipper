# Video requirements: a campaign brief applied to a job (`video_requirements.py`)

`/api/process` takes an optional `video_requirements` field (an object, or its JSON
string on an upload). The dashboard's "video requirements" panel sends it and keeps
the values across jobs, because one campaign usually spans several videos.

| Field | Handled by | Holds |
|---|---|---|
| `logo` (PNG/JPEG/WebP data URL), `logo_position`, `logo_width` | code, burned on | always, when the overlay encode succeeds |
| `link`, `link_on_video` | code, descriptions and optionally burned on | always; a burned link is not clickable |
| `overlay_text` | code, burned on | always, when the overlay encode succeeds |
| `cta_text` | code, burned on for the clip's last `CTA_SECONDS` (4 s) | always, when the overlay encode succeeds; empty means none |
| `description_text` | code, appended to the TikTok and Instagram descriptions | always |
| `must_include`, `must_avoid` | the model, through the selection prompts | guidance |

The rules go to the score, detail and visual prompts (`prompt_block`), appended after
`str.format`. Clip picks mostly read the transcript, so rules about what is on screen
hold less reliably than rules about what is said. Nothing checks a finished clip
against them yet, and no clip is flagged when required material is missing.

- **Layer order**: canonical → hook → captions → `brand_` → `wm_`. The canonical
  stays clean. The whole overlay is one Pillow-drawn PNG, so user text never reaches
  an ffmpeg filter string.
- **Editor**: `app._deliver` re-applies the overlay on every edit, and
  `_strip_watermark_copy` drops `brand_` with `wm_` in the walk-backs. A translated
  clip made from a branded file is not branded twice (`carries_brand`).
- **Free plan**: with an overlay the `wm_` copy cannot ride the caption encode (it
  would miss the logo), so `mark_delivery` runs its own pass over the `brand_` file.
- **Storage**: `.video_requirements.json` and `.brand_logo.png` in the job directory.
  Dotfiles are never served and survive an auto-retry. They are not archived to R2, so
  an edit on a job restored from R2 serves the clip without re-adding the overlay.
- **Failure**: a failed overlay encode logs a warning and delivers the clip without
  it, like the other delivery layers.
- **Logo size**: the server caps it at 700 KB because Starlette limits a multipart
  field to 1 MB. The dashboard resizes to 512 px first.
