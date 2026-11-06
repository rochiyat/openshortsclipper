# Campaign briefs, recommendations and earnings (Studio, self-host)

Three helpers on top of Studio's campaigns, posts and view snapshots
(`docs/plans/studio-roadmap.md`). Everything a model produces is a suggestion the
user reviews and saves; every number that decides money is computed in code.

## Reading a brief (`studio/campaign_ai.py`, `POST /api/studio/campaign-ai/brief`)

- Input: pasted text, or a page URL read through a **konten** browser account
  (`browser_accounts.py`, platform `konten`, its login window opens `KONTEN_URL`,
  default `https://konten.com/`). A pre-registry profile at
  `<root>/konten-connector/browser_profile` is taken in as `konten-default`.
  konten accounts are only read, never posted to (`browser_accounts.POST_PLATFORMS`).
- Reading a page acts as that login, so it answers only like browser posting does
  (`BROWSER_POST_ENABLED`, `browser_post.is_local_request`), only over https, only
  for hosts in `CAMPAIGN_READ_HOSTS` (default `konten.com` and its subdomains), and
  only for a public address (`security_utils.assert_public_url`). The worker
  (`browser_post._read_flow`) opens the page, waits, and returns `innerText`;
  nothing on the page is clicked. Selectors are never used, so a redesign of the
  site does not break it; a login page comes back as "not a brief".
- Page text goes to the model between `<<<` and `>>>` as data; the fence markers are
  stripped from the text first.
- The model fills `BriefModel`; `clean_brief` keeps only what passes: numbers in
  range, real dates, 3-letter currencies, known platforms, http(s) links. Missing
  means null, never a guess. The result fills the campaign form
  (`campaign_fields`) and the Clip Generator's video requirements
  (`video_requirements_from`, validated by `video_requirements.parse`).
- Saved on the campaign as `campaigns.brief` (migration 6). "use for next job"
  writes `os_video_requirements` in the browser, which `MediaInput` reads on mount.

## Checking a clip (`POST /api/studio/campaign-ai/check/{clip_id}`)

Code checks hashtags, mentions, link and length against the saved copy. The model
judges must include / must avoid / language from the copy and the pick reason
only (Studio keeps no transcript), and answers "unclear" when the copy does not
settle a rule. A code "fail" always fails the verdict.

## Recommending campaigns (`POST /api/studio/campaign-ai/recommend`)

Ranks the saved active campaigns plus unsaved ones read from a list page
(`/campaign-ai/list`). The model gives a 0-100 fit with reasons and risks; the
expected payout of a typical post is computed from the creator's own median
views per platform (live posts' latest snapshots) with the campaign's CPM,
minimum and cap. Ended campaigns sort last.

## Earnings (`studio/revenue.py`, `GET /api/studio/revenue`)

Per campaign: posts, eligible posts, views, payout, payout per post, eligible
posts not submitted (`claim_status` none), posts near the minimum (60-99%),
posts at the cap, days left. Totals are per currency and never mixed.
Recommendations, each naming its numbers: submit unclaimed posts, push posts near
the minimum, campaigns ending within 3 days, ended campaigns still active, the
cap, the best campaign per post and ones earning under a quarter of it (same
currency, 3+ posts each), the channel and clip length with the best median views,
and live posts without a view count from the last 48 h.
