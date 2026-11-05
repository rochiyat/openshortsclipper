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
import subprocess
import sys
import threading
import time
import uuid

_TRUE = ("1", "true", "yes", "on")

PLATFORMS = ("youtube",)
VISIBILITIES = ("public", "unlisted", "private")
REQUEST_PREFIX = "browser_"

# Worker: longest wait for the file to reach YouTube. API: kills a worker that
# overruns it, so it must stay above the sum of the worker's own waits.
UPLOAD_WAIT_SECONDS = 15 * 60
WORKER_TIMEOUT_SECONDS = 25 * 60

_posts: dict = {}
_posts_lock = threading.Lock()
# One upload per platform at a time: a browser profile can only be open once.
_platform_locks = {p: threading.Lock() for p in PLATFORMS}


def enabled() -> bool:
    return os.environ.get("BROWSER_POST_ENABLED", "").strip().lower() in _TRUE


def profile_dir(platform: str) -> str:
    """The persistent browser profile a platform's login lives in."""
    root = os.path.abspath(os.environ.get("BROWSER_POST_PROFILE_ROOT", "skills"))
    return os.path.join(root, f"{platform}-connector", "browser_profile")


def available_platforms() -> list:
    """Platforms that can post now. Only checks a profile exists: whether its
    login is still good is known when a post opens it."""
    if importlib.util.find_spec("playwright") is None:
        return []
    return [p for p in PLATFORMS if os.path.isdir(profile_dir(p))]


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


def _finish(request_id: str, platform: str, result: dict):
    with _posts_lock:
        _posts[request_id] = {
            "status": "completed" if result.get("success") else "failed",
            "results": [{
                "platform": platform,
                "success": bool(result.get("success")),
                "message": result.get("message"),
                "url": result.get("url"),
            }],
        }


def _run_worker(job: dict) -> dict:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        proc = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--worker"],
            input=json.dumps(job).encode("utf-8"), capture_output=True,
            timeout=WORKER_TIMEOUT_SECONDS, env=env,
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "message": "The browser upload took too long and was stopped."}
    lines = proc.stdout.decode("utf-8", "replace").strip().splitlines()
    try:
        result = json.loads(lines[-1])
        if isinstance(result, dict):
            return result
    except (IndexError, ValueError):
        pass
    tail = proc.stderr.decode("utf-8", "replace").strip()[-300:]
    return {"success": False, "message": f"The browser could not start: {tail or 'no output'}"}


def start(platform: str, *, file_path: str, title: str, description: str,
          visibility: str = "public", thumbnail_path: str = None) -> str:
    """Start an upload in the background and return its request id."""
    request_id = f"{REQUEST_PREFIX}{uuid.uuid4().hex}"
    with _posts_lock:
        _posts[request_id] = {"status": "in_progress", "results": []}
    job = {"platform": platform, "profile_dir": profile_dir(platform), "file_path": file_path,
           "title": title, "description": description, "visibility": visibility,
           "thumbnail_path": thumbnail_path}

    def run():
        with _platform_locks[platform]:
            try:
                result = _run_worker(job)
            except Exception as e:  # the status must always reach a final state
                result = {"success": False, "message": str(e)}
        _finish(request_id, platform, result)

    threading.Thread(target=run, name=f"browser-post-{platform}", daemon=True).start()
    return request_id


# --- Worker side ------------------------------------------------------------
# Studio follows the account's language, so nothing below reads visible text.
YT_UPLOAD_URL = "https://www.youtube.com/upload"
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


def _yt_open_upload_dialog(page, file_path: str):
    page.goto(YT_UPLOAD_URL, wait_until="domcontentloaded", timeout=60_000)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if _is_google_challenge(page.url):
            raise _PostFailed("Google is asking you to sign in or verify. Log in to the YouTube "
                              "profile again, then retry.")
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
    _yt_open_upload_dialog(page, job["file_path"])
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


def _run_job(job: dict) -> dict:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                job["profile_dir"], headless=False, viewport={"width": 1280, "height": 850})
            try:
                page = context.pages[0] if context.pages else context.new_page()
                return _youtube_flow(page, job)
            finally:
                context.close()
    except _PostFailed as e:
        return {"success": False, "message": str(e)}
    except PlaywrightError as e:
        first_line = (str(e).strip().splitlines() or ["unknown error"])[0]
        return {"success": False, "message": f"The YouTube Studio page did not behave as expected: {first_line}"}


if __name__ == "__main__":
    if "--worker" in sys.argv:
        print(json.dumps(_run_job(json.loads(sys.stdin.buffer.read().decode("utf-8")))))
