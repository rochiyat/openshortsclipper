# Browser posting

An alternative to Upload-Post for self-host: `browser_post.py` publishes a clip by
driving the platform's own web UI with Playwright, using a browser profile that is
already logged in on this machine. YouTube and TikTok so far, "publish now" only.

## Turning it on

It is off by default. It needs all of:

- `BROWSER_POST_ENABLED=1`
- `pip install playwright` and `playwright install chromium` (deliberately not in
  `requirements.txt`, the Docker image has no use for it)
- a logged-in profile at `<BROWSER_POST_PROFILE_ROOT>/<platform>-connector/browser_profile`
  (`BROWSER_POST_PROFILE_ROOT` defaults to `skills`)
- the API running directly on the machine with a display, not in Docker: the
  upload opens a visible window

The post modal then offers "post with: upload-post / browser".

## How it works

`/api/social/post` with `provider: "browser"` returns a `browser_…` request id at
once, and a thread runs `python browser_post.py --worker` (job as JSON on stdin,
result as JSON on stdout). `/api/social/post-status/{id}` answers from memory, in
the same shape as Upload-Post, so a restart forgets posts in progress. A post to
several platforms runs them one after the other, one worker each, and fails if any
fails while each result says which went out. Uploads to one platform run one at a
time, because a profile can only be open once.

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
- TikTok's Post is pressed once, because pressing it twice could post twice. The upload
  page closing is the confirmation; without it the result says to check Studio before
  posting again. A dialog after Post is reported, never answered: it may be a content
  warning that is the user's to accept. Selectors use `data-e2e`, and the caption is
  read back and compared before anything is posted.
- TikTok's visibilities are public, friends and private; YouTube's are public, unlisted
  and private. A post to both offers only what both can do.
- A chosen thumbnail is handed to Studio's own file input before Next. Custom Shorts
  thumbnails reach channels gradually, so a channel without the input still posts the
  video and the result says YouTube picked the thumbnail. TikTok does not take one yet.

## Limits

Automating a platform's web UI can break its terms and put the account at risk, and
any Studio redesign can break the flow until the selectors are updated. Tests cover
the gate and the status reporting, not the selectors: only a real upload proves them.
