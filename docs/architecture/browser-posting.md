# Browser posting

An alternative to Upload-Post for self-host: `browser_post.py` publishes a clip by
driving the platform's own web UI with Playwright, using a browser profile that is
already logged in on this machine. YouTube only so far, "publish now" only.

## Turning it on

It is off by default. It needs all of:

- `BROWSER_POST_ENABLED=1`
- `pip install playwright` and `playwright install chromium` (deliberately not in
  `requirements.txt`, the Docker image has no use for it)
- at least one saved account (below); `BROWSER_POST_PROFILE_ROOT` defaults to `skills`
- the API running directly on the machine with a display, not in Docker: the
  upload opens a visible window

The post modal then offers "post with: upload-post / browser".

## Accounts and channels (`browser_accounts.py`)

Accounts of platform `konten` are only read, for campaign briefs (`docs/architecture/campaign-ai.md`).

Each account is one persistent browser profile, saved in
`<root>/browser_accounts.json` with its profile at
`<root>/browser-accounts/<id>/browser_profile`. A profile from before the registry
(`<root>/<platform>-connector/browser_profile`) is taken in once as the account
`<platform>-default`; deleting it removes that folder and it is not taken in again.

- **Add**: `POST /api/social/browser/accounts` makes a fresh profile and opens a
  visible window on YouTube Studio. The person logs in and opens Studio on each
  channel to use (avatar → switch account), then closes the window. Every
  `studio.youtube.com/channel/UC…` URL a tab showed is added to the account.
  Nothing in the window is clicked or filled by the code.
- **Login again**: `POST …/accounts/{id}/login` reopens the window, to log back in
  or to visit more channels. Channels can also be added by hand (an id or any URL
  that contains one) and renamed with `PATCH …/accounts/{id}`.
- **Delete**: `DELETE …/accounts/{id}` forgets the account and deletes its profile,
  which logs it out on this machine. Only paths inside the profile root are deleted.
- **Posting**: `/api/social/post` takes `targets: [{account_id, channel_id?, title?,
  description?}]`, at most 10. A channel must be one of the account's. Targets go
  one after another in one request; the status lists one result per target
  (`target`, `account_id`, `channel_id`), and the request is `completed` when at
  least one succeeded. Without `targets`, the first account of each platform is used.
- **Right channel or nothing**: with a channel, the upload opens
  `studio.youtube.com/channel/<id>/videos/upload` and the file is handed over only
  once Studio shows that channel (`ytcfg` `CHANNEL_ID` first, then the URL). Another
  channel fails the post before anything is uploaded.
- One browser per profile: posts to the same account wait their turn; a login
  window or a delete is refused (409) while the profile is open.

## How it works

`/api/social/post` with `provider: "browser"` returns a `browser_…` request id at
once, and a thread runs `python browser_post.py --worker` (job as JSON on stdin,
result as JSON on stdout). `/api/social/post-status/{id}` answers from memory, in
the same shape as Upload-Post, so a restart forgets posts in progress. Uploads to
one account run one at a time, because a profile can only be open once.

## Rules

- `_browser_post_allowed` is the only gate: it acts as the logged-in user with no
  key to check, so it refuses Cloud, other machines, and other sites' pages (the
  self-host CORS is a wildcard).
- The window stays visible: Studio refuses headless browsers, and a verification
  has to reach the user. A CAPTCHA or Google challenge is reported as a failure,
  never worked around, and nothing hides the automation.
- Selectors must not depend on visible text: Studio follows the account's language.
- The upload is complete on the `x-goog-upload-status: final` response. Closing
  the browser earlier cancels it.
- Done is pressed until Studio confirms: one press has left a video a draft with no
  error. The confirmation is a dialog naming the video, over an upload dialog that
  stays in the page, so "the dialog closed" alone is not the signal.
- "Made for kids" is the channel owner's declaration: a channel default is kept,
  and only an unanswered question is set to "not made for kids".
- A chosen thumbnail is handed to Studio's own file input before Next. Custom Shorts
  thumbnails reach channels gradually, so a channel without the input still posts the
  video and the result says YouTube picked the thumbnail.

## Limits

Automating a platform's web UI can break its terms and put the account at risk, and
any Studio redesign can break the flow until the selectors are updated. Tests cover
the gate, the accounts and the status reporting, not the selectors: only a real
upload proves them, including that Studio opens a channel from its URL.
