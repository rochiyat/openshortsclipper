"""The browser-posting accounts saved on this machine (self-host, opt-in).

Each account is one persistent browser profile that a person logged in to by
hand. A Google account can manage several YouTube channels, so an account
holds the channels seen in its Studio; a post names the account and, for
YouTube, the channel. See docs/architecture/browser-posting.md.

The registry is a JSON file next to the profiles
(`<BROWSER_POST_PROFILE_ROOT>/browser_accounts.json`). A profile made before
the registry existed (`<root>/<platform>-connector/browser_profile`) is taken
in once as an ordinary account, so a session already logged in keeps working.
"""
import json
import os
import re
import shutil
import threading
import time
import uuid

PLATFORMS = ("youtube",)
REGISTRY_NAME = "browser_accounts.json"
ACCOUNTS_DIR = "browser-accounts"
CHANNEL_ID_RE = re.compile(r"UC[0-9A-Za-z_-]{22}")
ACCOUNT_ID_RE = re.compile(r"[a-z0-9-]{1,40}")
LABEL_MAX = 60
CHANNEL_NAME_MAX = 80
MAX_CHANNELS = 50

_lock = threading.RLock()


class AccountError(ValueError):
    pass


def root() -> str:
    return os.path.abspath(os.environ.get("BROWSER_POST_PROFILE_ROOT", "skills"))


def legacy_profile_dir(platform: str) -> str:
    return os.path.join(root(), f"{platform}-connector", "browser_profile")


def _registry_path() -> str:
    return os.path.join(root(), REGISTRY_NAME)


def _clean(text, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def channel_id_from(value) -> str:
    """A channel id from an id or any YouTube / Studio channel URL, or ""."""
    match = CHANNEL_ID_RE.search(str(value or ""))
    return match.group(0) if match else ""


def _read() -> dict:
    try:
        with open(_registry_path(), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("accounts"), list):
            return data
    except (OSError, ValueError):
        pass
    return {"accounts": [], "legacy_imported": []}


def _write(data: dict):
    os.makedirs(root(), exist_ok=True)
    tmp = f"{_registry_path()}.{uuid.uuid4().hex}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, _registry_path())


def _load() -> dict:
    """The registry, after taking in any pre-registry profile once."""
    data = _read()
    imported = data.setdefault("legacy_imported", [])
    changed = False
    for platform in PLATFORMS:
        if platform in imported:
            continue
        if os.path.isdir(legacy_profile_dir(platform)):
            data["accounts"].append({
                "id": f"{platform}-default", "platform": platform, "label": f"{platform} (existing login)",
                "profile": os.path.relpath(legacy_profile_dir(platform), root()),
                "channels": [], "created_at": int(time.time()),
            })
            imported.append(platform)
            changed = True
    if changed:
        _write(data)
    return data


def profile_dir(account: dict) -> str:
    return os.path.join(root(), account["profile"])


def _public(account: dict) -> dict:
    return {
        "id": account["id"], "platform": account["platform"], "label": account["label"],
        "channels": [dict(c) for c in account.get("channels", [])],
        "has_profile": os.path.isdir(profile_dir(account)),
        "created_at": account.get("created_at"),
        "last_login_at": account.get("last_login_at"),
    }


def list_accounts() -> list:
    with _lock:
        return [_public(a) for a in _load()["accounts"]]


def _find(data: dict, account_id: str) -> dict:
    for account in data["accounts"]:
        if account["id"] == account_id:
            return account
    raise KeyError(account_id)


def get(account_id: str) -> dict:
    """The stored record (with its profile path), or KeyError."""
    with _lock:
        return dict(_find(_load(), str(account_id)))


def create(platform: str, label: str) -> dict:
    if platform not in PLATFORMS:
        raise AccountError(f"Browser posting does not support {platform} yet")
    label = _clean(label, LABEL_MAX) or f"{platform} account"
    with _lock:
        data = _load()
        account_id = f"{platform}-{uuid.uuid4().hex[:8]}"
        account = {
            "id": account_id, "platform": platform, "label": label,
            "profile": os.path.join(ACCOUNTS_DIR, account_id, "browser_profile"),
            "channels": [], "created_at": int(time.time()),
        }
        os.makedirs(profile_dir(account), exist_ok=True)
        data["accounts"].append(account)
        _write(data)
        return _public(account)


def _channels(value) -> list:
    if not isinstance(value, list):
        raise AccountError("channels must be a list")
    seen, out = set(), []
    for item in value:
        item = item if isinstance(item, dict) else {"id": item}
        cid = channel_id_from(item.get("id"))
        if not cid:
            raise AccountError(f"Not a YouTube channel id or URL: {str(item.get('id'))[:80]}")
        if cid in seen:
            continue
        seen.add(cid)
        out.append({"id": cid, "name": _clean(item.get("name"), CHANNEL_NAME_MAX) or cid})
    if len(out) > MAX_CHANNELS:
        raise AccountError(f"At most {MAX_CHANNELS} channels per account")
    return out


def update(account_id: str, *, label=None, channels=None) -> dict:
    with _lock:
        data = _load()
        account = _find(data, account_id)
        if label is not None:
            account["label"] = _clean(label, LABEL_MAX) or account["label"]
        if channels is not None:
            account["channels"] = _channels(channels)
        _write(data)
        return _public(account)


def record_login(account_id: str, channel_ids) -> dict:
    """After a login window closes: keep known names, add the channels seen."""
    with _lock:
        data = _load()
        account = _find(data, account_id)
        known = {c["id"]: c for c in account.get("channels", [])}
        for cid in channel_ids or []:
            cid = channel_id_from(cid)
            if cid and cid not in known and len(known) < MAX_CHANNELS:
                known[cid] = {"id": cid, "name": cid}
        account["channels"] = list(known.values())
        account["last_login_at"] = int(time.time())
        _write(data)
        return _public(account)


def delete(account_id: str):
    """Forget the account and delete its browser profile (its login)."""
    with _lock:
        data = _load()
        account = _find(data, account_id)
        path = os.path.realpath(profile_dir(account))
        base = os.path.realpath(root())
        if os.path.commonpath([path, base]) != base or path == base:
            raise AccountError("The profile is outside the profile folder; remove it by hand")
        data["accounts"] = [a for a in data["accounts"] if a["id"] != account_id]
        _write(data)
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
        parent = os.path.dirname(path)  # browser-accounts/<id> or <platform>-connector
        if os.path.basename(path) == "browser_profile" and os.path.isdir(parent) and not os.listdir(parent):
            os.rmdir(parent)
