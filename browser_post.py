"""Post a clip through a browser logged in on this machine (self-host, opt-in).

The alternative to Upload-Post: no API key, but it drives the platform's own web
UI, so it breaks when that UI changes. See docs/architecture/browser-posting.md.

Each upload runs in a child process (`python browser_post.py --worker`, job as
JSON on stdin, result as JSON on stdout): Playwright's event loop stays out of
the API's, and a stuck browser cannot take the API down with it. The window is
visible on purpose: Studio refuses headless browsers, and if Google asks the
user to verify, they have to be able to see it. A challenge is reported as a
failure for the user to resolve, never worked around.
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid

import browser_accounts

_TRUE = ("1", "true", "yes", "on")

PLATFORMS = browser_accounts.POST_PLATFORMS
# Where a login window opens, per account platform.
LOGIN_URLS = {"youtube": "https://studio.youtube.com/",
              "tiktok": "https://www.tiktok.com/tiktokstudio",
              "konten": os.environ.get("KONTEN_URL", "https://konten.com/")}
LOCAL_HOSTS = ("127.0.0.1", "::1", "localhost")
VISIBILITIES = {
    "youtube": ("public", "unlisted", "private"),
    "tiktok": ("public", "friends", "private"),
}
REQUEST_PREFIX = "browser_"

# Worker: longest wait for the file to reach YouTube. API: kills a worker that
# overruns it, so it must stay above the sum of the worker's own waits.
UPLOAD_WAIT_SECONDS = 15 * 60
WORKER_TIMEOUT_SECONDS = 25 * 60
# A login window stays open until the person closes it, up to this long.
LOGIN_WINDOW_SECONDS = 20 * 60
LOGIN_TIMEOUT_SECONDS = LOGIN_WINDOW_SECONDS + 2 * 60

_posts: dict = {}
_posts_lock = threading.Lock()
# One browser per profile at a time: a profile can only be open once. Posts
# wait their turn; a login window is refused while the profile is busy.
_profile_locks: dict = {}
_logins: dict = {}


def enabled() -> bool:
    return os.environ.get("BROWSER_POST_ENABLED", "").strip().lower() in _TRUE


def is_local_request(request) -> bool:
    """From this machine, and not from a web page on another site (the API's
    self-host CORS is a wildcard). A logged-in browser answers to nothing else."""
    from urllib.parse import urlparse
    if not request.client or request.client.host not in LOCAL_HOSTS:
        return False
    origin = request.headers.get("origin")
    return not origin or urlparse(origin).hostname in LOCAL_HOSTS


def playwright_installed() -> bool:
    return importlib.util.find_spec("playwright") is not None


def _profile_lock(account_id: str) -> threading.Lock:
    with _posts_lock:
        return _profile_locks.setdefault(account_id, threading.Lock())


def accounts() -> list:
    """Saved accounts, each with whether a login window is open for it now."""
    out = []
    for account in browser_accounts.list_accounts():
        with _posts_lock:
            login = dict(_logins.get(account["id"]) or {})
        out.append({**account, "login": login or None})
    return out


def available_platforms() -> list:
    """Platforms that can post now. Only checks a profile exists: whether its
    login is still good is known when a post opens it."""
    if not playwright_installed():
        return []
    have = {a["platform"] for a in browser_accounts.list_accounts() if a["has_profile"]}
    return [p for p in PLATFORMS if p in have]


def clean_text(text, limit: int) -> str:
    """YouTube rejects angle brackets in titles and descriptions."""
    return str(text or "").replace("<", "").replace(">", "").strip()[:limit]


def is_browser_request(request_id: str) -> bool:
    return str(request_id).startswith(REQUEST_PREFIX)


def status(request_id: str) -> dict:
    """Same shape as Upload-Post's status, which the dashboard already follows."""
    with _posts_lock:
        post = _posts.get(request_id)
        return dict(post) if post else {"status": "unknown", "results": []}


def _result(target: dict, result: dict) -> dict:
    return {
        "platform": target["platform"],
        "account_id": target["account_id"],
        "channel_id": target.get("channel_id"),
        "target": target["label"],
        "success": bool(result.get("success")),
        "message": result.get("message"),
        "url": result.get("url"),
    }


def _add_result(request_id: str, entry: dict, final: bool):
    with _posts_lock:
        post = _posts[request_id]
        post["results"] = [*post["results"], entry]
        if final:
            ok = [r["success"] for r in post["results"]]
            post["status"] = "completed" if any(ok) else "failed"


def _run_worker(job: dict, timeout: int = WORKER_TIMEOUT_SECONDS) -> dict:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        proc = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--worker"],
            input=json.dumps(job).encode("utf-8"), capture_output=True,
            timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "message": "The browser took too long and was stopped."}
    lines = proc.stdout.decode("utf-8", "replace").strip().splitlines()
    try:
        result = json.loads(lines[-1])
        if isinstance(result, dict):
            return result
    except (IndexError, ValueError):
        pass
    tail = proc.stderr.decode("utf-8", "replace").strip()[-300:]
    return {"success": False, "message": f"The browser could not start: {tail or 'no output'}"}


def start(targets: list, *, file_path: str, visibility: str = "public",
          thumbnail_path: str = None) -> str:
    """Start uploading one file to each target in the background, one after
    another, and return the request id. A target is a dict with platform,
    account_id, profile_dir, label, title, description and, for YouTube, an
    optional channel_id."""
    request_id = f"{REQUEST_PREFIX}{uuid.uuid4().hex}"
    with _posts_lock:
        _posts[request_id] = {"status": "in_progress", "results": []}

    def run():
        for n, target in enumerate(targets):
            job = {"mode": "post", "platform": target["platform"], "profile_dir": target["profile_dir"],
                   "channel_id": target.get("channel_id"), "file_path": file_path,
                   "title": target["title"], "description": target["description"],
                   "visibility": visibility, "thumbnail_path": thumbnail_path}
            try:
                with _profile_lock(target["account_id"]):
                    result = _run_worker(job)
            except Exception as e:  # the status must always reach a final state
                result = {"success": False, "message": str(e)}
            _add_result(request_id, _result(target, result), final=n == len(targets) - 1)

    threading.Thread(target=run, name="browser-post", daemon=True).start()
    return request_id


class Busy(Exception):
    pass


def open_login(account_id: str):
    """Open a visible window on the account's profile for the person to log in
    and visit each channel in Studio. When they close it, the Studio channels
    seen are added to the account. Raises Busy while the profile is in use."""
    account = browser_accounts.get(account_id)
    lock = _profile_lock(account_id)
    if not lock.acquire(blocking=False):
        raise Busy("This account's browser is already open (a login window or an upload).")
    with _posts_lock:
        _logins[account_id] = {"status": "open", "message": None}
    job = {"mode": "login", "platform": account["platform"],
           "profile_dir": browser_accounts.profile_dir(account),
           "url": LOGIN_URLS.get(account["platform"], LOGIN_URLS["youtube"])}

    def run():
        try:
            try:
                result = _run_worker(job, timeout=LOGIN_TIMEOUT_SECONDS)
            except Exception as e:
                result = {"success": False, "message": str(e)}
            channels = result.get("channels") or []
            if result.get("success"):
                try:
                    browser_accounts.record_login(account_id, channels)
                except KeyError:  # deleted while the window was open
                    pass
            found = len(channels)
            if not result.get("success"):
                message = result.get("message")
            elif account["platform"] != "youtube":
                message = "Login window closed."
            elif found:
                message = f"{found} channel{'s' if found != 1 else ''} seen in YouTube Studio."
            else:
                message = ("No YouTube Studio channel was seen: add channels by hand, "
                           "or open the window again and visit YouTube Studio.")
            with _posts_lock:
                _logins[account_id] = {"status": "done" if result.get("success") else "failed",
                                       "message": message}
        finally:
            lock.release()

    threading.Thread(target=run, name=f"browser-login-{account_id}", daemon=True).start()


READ_TIMEOUT_SECONDS = 3 * 60
READ_TEXT_MAX = 60_000


def read_page(account_id: str, url: str) -> dict:
    """The visible text of ``url`` opened in the account's logged-in profile,
    for a brief or a campaign list. Blocking; waits while the profile is busy.
    Returns {success, title, url, text} or {success: False, message}."""
    account = browser_accounts.get(account_id)
    job = {"mode": "read", "platform": account["platform"],
           "profile_dir": browser_accounts.profile_dir(account), "url": url}
    with _profile_lock(account_id):
        return _run_worker(job, timeout=READ_TIMEOUT_SECONDS)


def delete_account(account_id: str):
    """Delete an account and its login, unless its browser is open right now."""
    lock = _profile_lock(account_id)
    if not lock.acquire(blocking=False):
        raise Busy("This account's browser is open: close it or wait for the upload, then delete.")
    try:
        browser_accounts.delete(account_id)
        with _posts_lock:
            _logins.pop(account_id, None)
    finally:
        lock.release()


# --- Worker side ------------------------------------------------------------
# Both studios follow the account's language, so nothing below reads visible text.
YT_UPLOAD_URL = "https://www.youtube.com/upload"
YT_CHANNEL_UPLOAD_URL = "https://studio.youtube.com/channel/{}/videos/upload?d=ud"
YT_STUDIO_URL = "https://studio.youtube.com/"
STUDIO_CHANNEL_RE = re.compile(r"^https://studio\.youtube\.com/channel/(UC[0-9A-Za-z_-]{22})")
YT_FILE_INPUT = "input[type=file]"
YT_TITLE = "#title-textarea #textbox"
YT_DESCRIPTION = "#description-textarea #textbox"
YT_THUMBNAIL_INPUT = "ytcp-thumbnail-uploader input[type=file]"
YT_NOT_FOR_KIDS = 'tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]'
YT_FOR_KIDS = 'tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_MFK"]'
YT_NEXT = "#next-button"
YT_DONE = "#done-button"
YT_VIDEO_LINK = "ytcp-uploads-dialog a[href*='youtu']"
YT_ERROR = "ytcp-uploads-dialog .error-short"
YT_STEPS_TO_VISIBILITY = 3
DONE_RETRY_SECONDS = 15

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
except ImportError:  # optional dependency: only the worker needs it
    PlaywrightTimeoutError = TimeoutError


class _PostFailed(Exception):
    pass


def _is_upload_complete(response) -> bool:
    return ("upload.youtube.com" in response.url
            and response.headers.get("x-goog-upload-status") == "final")


def _is_google_challenge(url: str) -> bool:
    return "accounts.google.com" in url


def _yt_dialog_error(page) -> str:
    box = page.locator(YT_ERROR).first
    if box.count() and box.is_visible():
        return box.inner_text().strip()
    return ""


def studio_channel(url: str) -> str:
    match = STUDIO_CHANNEL_RE.match(str(url or ""))
    return match.group(1) if match else ""


def _yt_active_channel(page) -> str:
    """The channel Studio is acting as: its own config first, the URL second."""
    try:
        from_config = page.evaluate(
            "() => { try { return String((window.ytcfg && ytcfg.get && ytcfg.get('CHANNEL_ID')) || '') }"
            " catch (e) { return '' } }")
    except Exception:
        from_config = ""
    return browser_accounts.channel_id_from(from_config) or studio_channel(page.url)


def _yt_open_upload_dialog(page, file_path: str, channel_id: str = None):
    """Open the upload dialog, on ``channel_id`` when one is named. A file is
    only handed over once Studio shows that channel: a Google account switches
    between its channels, and a post must never land on the wrong one."""
    url = YT_CHANNEL_UPLOAD_URL.format(channel_id) if channel_id else YT_UPLOAD_URL
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if _is_google_challenge(page.url):
            raise _PostFailed("Google is asking you to sign in or verify. Log in to the YouTube "
                              "profile again, then retry.")
        if channel_id:
            active = _yt_active_channel(page)
            if not active:
                page.wait_for_timeout(1000)
                continue
            if active != channel_id:
                raise _PostFailed(
                    f"YouTube Studio opened channel {active} instead of {channel_id}, so nothing was "
                    "uploaded. Check that this login manages the channel: open the account's login "
                    "window, switch to the channel in Studio once, and close it.")
        if page.locator(YT_FILE_INPUT).count():
            page.locator(YT_FILE_INPUT).first.set_input_files(file_path)
            return
        page.wait_for_timeout(1000)
    raise _PostFailed("YouTube Studio's upload dialog did not open.")


def _yt_fill_details(page, job: dict):
    page.locator(YT_TITLE).wait_for(state="visible", timeout=120_000)
    page.locator(YT_TITLE).fill(job["title"])
    if job["description"]:
        page.locator(YT_DESCRIPTION).fill(job["description"])
    # "Made for kids" is the channel owner's legal declaration: a default set on
    # the channel is kept, and only an unanswered question gets "not for kids".
    answered = any(page.locator(s).get_attribute("aria-checked") == "true"
                   for s in (YT_FOR_KIDS, YT_NOT_FOR_KIDS))
    if not answered:
        page.locator(YT_NOT_FOR_KIDS).click()


def _yt_set_thumbnail(page, thumbnail_path: str) -> bool:
    """Hand Studio the thumbnail. False when the page offers no way to: custom
    Shorts thumbnails reach channels gradually, and the video still has to go out."""
    uploader = page.locator(YT_THUMBNAIL_INPUT).first
    try:
        uploader.wait_for(state="attached", timeout=15_000)
        uploader.set_input_files(thumbnail_path)
    except PlaywrightTimeoutError:
        return False
    page.wait_for_timeout(3000)
    return True


def _yt_wait_for_upload(page, uploaded: list):
    """Closing the browser before this point would cancel the upload."""
    deadline = time.monotonic() + UPLOAD_WAIT_SECONDS
    while not uploaded:
        error = _yt_dialog_error(page)
        if error:
            raise _PostFailed(f"YouTube: {error}")
        if time.monotonic() > deadline:
            raise _PostFailed("The upload did not finish in time. A draft may remain in YouTube Studio.")
        page.wait_for_timeout(2000)


def _yt_other_dialog_text(page) -> str:
    """Text of a visible dialog over the upload dialog, which would swallow Done."""
    return page.evaluate("""() => [...document.querySelectorAll('tp-yt-paper-dialog, ytcp-dialog')]
        .filter(d => d.offsetParent !== null && !d.closest('ytcp-uploads-dialog'))
        .map(d => d.innerText.trim()).filter(Boolean).join(' / ')""")[:300]


def _normalise(text: str) -> str:
    return " ".join(str(text or "").split()).casefold()


def _yt_confirms_post(dialog_text: str, title: str) -> bool:
    """Studio confirms a saved video with a dialog naming it, shown over an upload
    dialog that stays in the page. Its wording follows the account's language, so
    the video's own title is what identifies it."""
    return bool(title) and _normalise(title) in _normalise(dialog_text)


def _yt_finish(page, title: str):
    """Press Done until Studio confirms. One press was seen to leave the video a
    draft with no error at all, and pressing again is harmless."""
    page.locator(YT_DONE).click(timeout=120_000)
    deadline = time.monotonic() + 180
    next_press = time.monotonic() + DONE_RETRY_SECONDS
    while page.locator(YT_DONE).is_visible():
        if _yt_confirms_post(_yt_other_dialog_text(page), title):
            return
        error = _yt_dialog_error(page)
        if error:
            raise _PostFailed(f"YouTube: {error}")
        if _is_google_challenge(page.url):
            raise _PostFailed("Google is asking you to verify before publishing. The video is "
                              "still a draft in YouTube Studio.")
        if time.monotonic() > deadline:
            blocked_by = _yt_other_dialog_text(page)
            detail = f" YouTube shows: {blocked_by}" if blocked_by else ""
            raise _PostFailed(f"YouTube did not confirm the post, so it may still be a draft in "
                              f"YouTube Studio.{detail}")
        if time.monotonic() >= next_press:
            next_press = time.monotonic() + DONE_RETRY_SECONDS
            try:
                page.locator(YT_DONE).click(timeout=5_000)
            except PlaywrightTimeoutError:
                pass  # covered by another dialog, reported if it never clears
        page.wait_for_timeout(1000)


def _youtube_flow(page, job: dict) -> dict:
    uploaded = []
    page.on("response", lambda r: uploaded.append(True) if _is_upload_complete(r) else None)
    _yt_open_upload_dialog(page, job["file_path"], job.get("channel_id"))
    _yt_fill_details(page, job)
    thumbnail = job.get("thumbnail_path")
    thumbnail_set = bool(thumbnail) and _yt_set_thumbnail(page, thumbnail)
    for _ in range(YT_STEPS_TO_VISIBILITY):
        page.locator(YT_NEXT).click(timeout=60_000)
    page.locator(f'tp-yt-paper-radio-button[name="{job["visibility"].upper()}"]').click(timeout=60_000)
    link = page.locator(YT_VIDEO_LINK).first
    link.wait_for(state="attached", timeout=120_000)
    url = link.get_attribute("href")
    _yt_wait_for_upload(page, uploaded)
    _yt_finish(page, job["title"])
    message = f"Posted as {job['visibility']}."
    if thumbnail and not thumbnail_set:
        message += " YouTube Studio offered no thumbnail upload, so YouTube picked one."
    return {"success": True, "message": message, "url": url}


def _login_flow(context, page, url: str = None) -> dict:
    """Leave the window to the person: log in, open Studio on each channel to
    use (switching channel from the avatar menu), then close the window. Every
    Studio channel any tab showed is reported; nothing is clicked here."""
    seen = []
    closed = []
    context.on("close", lambda *_: closed.append(True))
    page.goto(url or YT_STUDIO_URL, wait_until="domcontentloaded", timeout=60_000)
    deadline = time.monotonic() + LOGIN_WINDOW_SECONDS
    while not closed and time.monotonic() < deadline:
        pages = list(context.pages)
        if not pages:
            break
        for tab in pages:
            cid = studio_channel(tab.url)
            if cid and cid not in seen:
                seen.append(cid)
        try:
            pages[0].wait_for_timeout(1000)
        except Exception:  # the tab or the window was closed meanwhile
            if not context.pages:
                break
    return {"success": True, "channels": seen}


def _read_flow(page, url: str) -> dict:
    """Open a page and hand back what a person would read on it. Nothing is
    clicked: a page that needs a login shows the login form's text, and the
    reader says so."""
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    try:
        page.wait_for_load_state("networkidle", timeout=20_000)
    except PlaywrightTimeoutError:
        pass  # pages that poll never go idle; what is there is read
    page.wait_for_timeout(1500)
    text = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
    return {"success": True, "title": page.title()[:300], "url": page.url,
            "text": text[:READ_TEXT_MAX]}


TT_UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload?from=upload"
TT_FILE_INPUT = "input[type=file]"
TT_CAPTION = '[data-e2e="caption_container"] [contenteditable="true"]'
TT_VISIBILITY = '[data-e2e="video_visibility_container"] button[role="combobox"]'
TT_POST = '[data-e2e="post_video_button"]'
TT_TOUR = ".react-joyride__overlay"
TT_TOUR_BUTTONS = '.tutorial-tooltip__footer button, [data-test-id="button-primary"]'
TT_CHALLENGE = '[id*="captcha" i], [class*="captcha" i]'
TT_DIALOG = '[role="dialog"]'
# TikTok's own values for "who can see this post".
TT_VISIBILITY_VALUES = {"public": "0", "friends": "2", "private": "1"}
TT_CAPTION_MAX = 4000
TT_POST_DEADLINE_SECONDS = 180


def _tt_check_blocked(page):
    """A login or verification is for the user to resolve, never worked around."""
    if "/login" in page.url:
        raise _PostFailed("TikTok is asking you to log in. Log in to the TikTok profile again, then retry.")
    challenge = page.locator(TT_CHALLENGE).first
    if challenge.count() and challenge.is_visible():
        raise _PostFailed("TikTok is asking you to verify. Complete it in TikTok Studio, then retry.")


def _tt_dismiss_tour(page):
    """The feature tour's overlay swallows every click until it is closed."""
    for _ in range(5):
        if not page.locator(TT_TOUR).count():
            return
        button = page.locator(TT_TOUR_BUTTONS).first
        if not button.count():
            return
        button.click(timeout=5_000)
        page.wait_for_timeout(800)


def _tt_open_upload(page, file_path: str):
    page.goto(TT_UPLOAD_URL, wait_until="domcontentloaded", timeout=60_000)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        _tt_check_blocked(page)
        if page.locator(TT_FILE_INPUT).count():
            page.locator(TT_FILE_INPUT).first.set_input_files(file_path)
            return
        page.wait_for_timeout(1000)
    raise _PostFailed("TikTok Studio's upload page did not open.")


def _compact(text: str) -> str:
    return "".join(str(text or "").split()).casefold()


def _tt_fill_caption(page, caption: str):
    """TikTok fills in the file's name. Its editor ignores a plain fill, so this
    replaces the text as a keyboard would, and refuses to post a garbled caption."""
    editor = page.locator(TT_CAPTION).first
    editor.wait_for(state="visible", timeout=120_000)
    _tt_dismiss_tour(page)
    editor.click(timeout=30_000)
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")
    page.wait_for_timeout(300)
    # The editor applies each change a moment later, and text sent sooner is lost.
    for i, line in enumerate(caption.split("\n")):
        if i:
            page.keyboard.press("Enter")
            page.wait_for_timeout(300)
        if line:
            page.keyboard.insert_text(line)
            page.wait_for_timeout(300)
    page.keyboard.press("Escape")  # a trailing hashtag leaves its suggestions open
    page.wait_for_timeout(500)
    if _compact(editor.inner_text()) != _compact(caption):
        raise _PostFailed("TikTok did not take the caption as written, so nothing was posted.")


def _tt_set_visibility(page, visibility: str):
    page.locator(TT_VISIBILITY).click(timeout=30_000)
    value = TT_VISIBILITY_VALUES[visibility]
    page.locator(f"""[role="option"][id='option-"{value}"']""").click(timeout=10_000)
    page.wait_for_timeout(500)


def _tt_wait_until_postable(page):
    """Post stays disabled while the file uploads."""
    button = page.locator(TT_POST)
    deadline = time.monotonic() + UPLOAD_WAIT_SECONDS
    while True:
        _tt_check_blocked(page)
        ready = (button.count() and button.get_attribute("aria-disabled") != "true"
                 and button.get_attribute("data-disabled") != "true"
                 and button.get_attribute("data-loading") != "true")
        if ready:
            return
        if time.monotonic() > deadline:
            raise _PostFailed("The upload to TikTok did not finish in time, so nothing was posted.")
        page.wait_for_timeout(2000)


def _tt_dialog_text(page) -> str:
    dialogs = page.locator(TT_DIALOG)
    texts = [dialogs.nth(i).inner_text().strip() for i in range(dialogs.count()) if dialogs.nth(i).is_visible()]
    return " / ".join(t for t in texts if t)[:300]


def _tt_post(page):
    """Press Post once: pressing it twice could post twice. Studio leaves the upload
    page when the post is accepted. A dialog asking anything is left for the user."""
    page.locator(TT_POST).click(timeout=30_000)
    deadline = time.monotonic() + TT_POST_DEADLINE_SECONDS
    while "/upload" in page.url:
        _tt_check_blocked(page)
        if time.monotonic() > deadline:
            shown = _tt_dialog_text(page)
            detail = f" TikTok shows: {shown}" if shown else ""
            raise _PostFailed("TikTok did not confirm the post. Check TikTok Studio before posting "
                              f"again, so it does not go out twice.{detail}")
        page.wait_for_timeout(1000)


def _tiktok_flow(page, job: dict) -> dict:
    caption = (job.get("description") or job["title"]).strip()[:TT_CAPTION_MAX]
    _tt_open_upload(page, job["file_path"])
    _tt_fill_caption(page, caption)
    _tt_set_visibility(page, job["visibility"])
    _tt_wait_until_postable(page)
    _tt_dismiss_tour(page)
    _tt_post(page)
    return {"success": True, "message": f"Posted as {job['visibility']}.", "url": None}


_FLOWS = {"youtube": ("YouTube Studio", _youtube_flow), "tiktok": ("TikTok Studio", _tiktok_flow)}


def _run_job(job: dict) -> dict:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    login = job.get("mode") == "login"
    try:
        with sync_playwright() as p:
            if login:
                os.makedirs(job["profile_dir"], exist_ok=True)
            elif not os.path.isdir(job["profile_dir"]):
                return {"success": False, "message": "This account has no browser profile: log in to it first."}
            context = p.chromium.launch_persistent_context(
                job["profile_dir"], headless=False, viewport={"width": 1280, "height": 850})
            try:
                page = context.pages[0] if context.pages else context.new_page()
                if login:
                    return _login_flow(context, page, job.get("url"))
                if job.get("mode") == "read":
                    return _read_flow(page, job["url"])
                return _FLOWS[job["platform"]][1](page, job)
            finally:
                try:
                    context.close()
                except PlaywrightError:
                    pass  # the person already closed it
    except _PostFailed as e:
        return {"success": False, "message": str(e)}
    except PlaywrightError as e:
        first_line = (str(e).strip().splitlines() or ["unknown error"])[0]
        if login:
            return {"success": False, "message": f"The login window failed: {first_line}"}
        if job.get("mode") == "read":
            return {"success": False, "message": f"The page could not be read: {first_line}"}
        studio = _FLOWS.get(job.get("platform"), ("The studio",))[0]
        return {"success": False, "message": f"The {studio} page did not behave as expected: {first_line}"}


if __name__ == "__main__":
    if "--worker" in sys.argv:
        print(json.dumps(_run_job(json.loads(sys.stdin.buffer.read().decode("utf-8")))))
