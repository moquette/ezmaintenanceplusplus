"""
EZ Maintenance++ : Dropbox destination (requests-only, NO SDK).

Talks to the Dropbox v2 REST API directly with script.module.requests so we add no
new dependency and vendor nothing. The add-on is registered as a Dropbox App-folder
app, so every path here is IN-APP-RELATIVE ("/name.zip", "" = the app folder root):
Dropbox scopes us to Apps/<app>/ and we must never send "/Apps/...".

Secrets contract: the App key/secret are baked into the constants below for the
one-tap experience; if left empty they fall back to the (whitespace-trimmed) settings
'dropbox_key'/'dropbox_secret'. The long-lived refresh token lives only in the setting
'dropbox_refresh_token'. None of key / secret / code / token is ever logged.

This program is free software: GPL v3 or later (see the other modules).
"""

import json
import time

import requests
import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

APP_KEY = ""  # baked-in Dropbox App-folder app key (placeholder; falls back to setting 'dropbox_key')
APP_SECRET = ""  # baked-in secret (placeholder; falls back to setting 'dropbox_secret')

# The real baked-in credentials ship in _appauth.py, which is gitignored (never committed)
# but IS included in the built install zip. If absent, _key()/_secret() fall back to the
# advanced settings fields so a user can paste their own Dropbox app key/secret.
try:
    from resources.lib.modules._appauth import APP_KEY, APP_SECRET
except Exception:
    pass

CHUNK = (
    50 * 1000 * 1000
)  # 50MB - xbmcbackup's memory-safe size for Fire TV/Apple TV (NOT 150MB)
OAUTH_AUTHORIZE = "https://www.dropbox.com/oauth2/authorize"
OAUTH_TOKEN = "https://api.dropboxapi.com/oauth2/token"
API = "https://api.dropboxapi.com/2"
CONTENT = "https://content.dropboxapi.com/2"

# connect + read timeout (read is long because chunk uploads/downloads stream)
TIMEOUT = (10, 300)

AddonID = "script.ezmaintenanceplusplus"
AddonTitle = "EZ Maintenance++"

_addon = xbmcaddon.Addon(id=AddonID)

# module-level in-memory bearer cache; exp is an absolute unix time
_cache = {"bearer": None, "exp": 0}


class DropboxAuthError(Exception):
    pass


def _log(msg):
    # NEVER pass a token / code / key / secret in here.
    xbmc.log("%s [dropbox] %s" % (AddonTitle, msg), level=xbmc.LOGINFO)


def _setting(key):
    try:
        return (xbmcaddon.Addon(id=AddonID).getSetting(key) or "").strip()
    except Exception:
        return ""


def _key():
    return APP_KEY if APP_KEY else _setting("dropbox_key")


def _secret():
    return APP_SECRET if APP_SECRET else _setting("dropbox_secret")


def _refresh_token():
    return _setting("dropbox_refresh_token")


def _set_refresh_token(token):
    xbmcaddon.Addon(id=AddonID).setSetting("dropbox_refresh_token", token)


def authorize():
    """Interactive one-time sign in: get a code, swap it for a refresh token, store it."""
    key, secret = _key(), _secret()
    if not key or not secret:
        xbmcgui.Dialog().ok(
            AddonTitle,
            "Dropbox is not set up in this build. Open the add-on settings and paste a "
            "Dropbox App key and secret (Advanced), then try Sign in again.",
        )
        return False

    auth_url = "%s?client_id=%s&response_type=code&token_access_type=offline" % (
        OAUTH_AUTHORIZE,
        key,
    )
    xbmcgui.Dialog().ok(
        AddonTitle,
        "1) On a phone or computer open:\n%s\n\n"
        "2) Approve access, copy the code Dropbox shows, then paste it on the next screen."
        % auth_url,
    )
    code = xbmcgui.Dialog().input("Paste the Dropbox code", type=xbmcgui.INPUT_ALPHANUM)
    if not code:
        return False
    code = code.strip()

    try:
        resp = requests.post(
            OAUTH_TOKEN,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": key,
                "client_secret": secret,
            },
            timeout=TIMEOUT,
        )
    except Exception as e:
        _log("authorize: token request failed: %s" % type(e).__name__)
        xbmcgui.Dialog().ok(
            AddonTitle, "Could not reach Dropbox. Check the network and try again."
        )
        return False

    if resp.status_code != 200:
        _log("authorize: token exchange rejected (HTTP %s)" % resp.status_code)
        xbmcgui.Dialog().ok(
            AddonTitle,
            "Dropbox did not accept that code. Make sure you copied the whole code and try Sign in again.",
        )
        return False

    data = resp.json()
    refresh = data.get("refresh_token", "")
    if not refresh:
        _log("authorize: response had no refresh_token")
        xbmcgui.Dialog().ok(
            AddonTitle, "Dropbox did not return a refresh token. Try Sign in again."
        )
        return False

    _set_refresh_token(refresh)
    # warm the bearer cache off the access token we just got
    expires_in = int(data.get("expires_in", 14400))
    bearer = data.get("access_token")
    if bearer:
        _cache["bearer"] = bearer
        _cache["exp"] = time.time() + expires_in
    _log("authorize: connected (refresh token stored)")
    xbmcgui.Dialog().ok(AddonTitle, "Connected to Dropbox.")
    return True


def _access_token(force=False):
    """Return a valid bearer token, refreshing via the stored refresh token as needed."""
    now = time.time()
    if not force and _cache["bearer"] and now < (_cache["exp"] - 300):
        return _cache["bearer"]

    refresh = _refresh_token()
    if not refresh:
        raise DropboxAuthError("not signed in to Dropbox")
    key, secret = _key(), _secret()
    if not key or not secret:
        raise DropboxAuthError("Dropbox app key/secret missing")

    try:
        resp = requests.post(
            OAUTH_TOKEN,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh,
                "client_id": key,
                "client_secret": secret,
            },
            timeout=TIMEOUT,
        )
    except Exception as e:
        raise DropboxAuthError("token refresh network error: %s" % type(e).__name__)

    if resp.status_code == 400:
        _log("access_token: refresh rejected (HTTP 400)")
        raise DropboxAuthError("Dropbox refresh token rejected; sign in again")
    if resp.status_code != 200:
        raise DropboxAuthError("token refresh failed (HTTP %s)" % resp.status_code)

    data = resp.json()
    bearer = data.get("access_token")
    if not bearer:
        raise DropboxAuthError("token refresh returned no access_token")
    _cache["bearer"] = bearer
    _cache["exp"] = now + int(data.get("expires_in", 14400))
    return bearer


def _auth_header(force=False):
    return {"Authorization": "Bearer %s" % _access_token(force=force)}


def _handle_retryable(resp):
    """Return True if the caller should retry once. Handles 401 (refresh) and 429 (sleep)."""
    if resp.status_code == 401:
        _access_token(force=True)
        return True
    if resp.status_code == 429:
        try:
            wait = int(resp.headers.get("Retry-After", "1")) + 1
        except Exception:
            wait = 2
        _log("rate limited; sleeping %ss" % wait)
        time.sleep(wait)
        return True
    return False


def _rpc(url, arg, force=False):
    """A JSON-body Dropbox RPC endpoint (api.dropboxapi.com)."""
    headers = _auth_header(force=force)
    headers["Content-Type"] = "application/json"
    return requests.post(url, headers=headers, data=json.dumps(arg), timeout=TIMEOUT)


# ----------------------------------------------------------------------------- upload


def upload(local_path, remote_name):
    """Upload a local file to /<remote_name> in the app folder, retrying the whole op once."""
    last_err = None
    for attempt in (1, 2):
        try:
            _do_upload(local_path, remote_name)
            return True
        except Exception as e:
            last_err = e
            _log("upload attempt %s failed: %s" % (attempt, type(e).__name__))
    if last_err:
        raise last_err
    return False


def _do_upload(local_path, remote_name):
    path = "/" + remote_name
    abs_local = xbmcvfs.translatePath(local_path)
    size = xbmcvfs.Stat(local_path).st_size()

    if size <= CHUNK:
        with xbmcvfs.File(local_path) as f:
            body = f.readBytes()
        arg = {"path": path, "mode": "overwrite", "mute": True}
        for attempt in (1, 2):
            headers = _auth_header(force=(attempt == 2))
            headers["Dropbox-API-Arg"] = json.dumps(arg)
            headers["Content-Type"] = "application/octet-stream"
            resp = requests.post(
                CONTENT + "/files/upload", headers=headers, data=body, timeout=TIMEOUT
            )
            if resp.status_code == 200:
                return
            if attempt == 1 and _handle_retryable(resp):
                continue
            raise DropboxAuthError("upload failed (HTTP %s)" % resp.status_code)
        return

    # large file: chunked upload session, streaming from disk one CHUNK at a time
    session_id = None
    offset = 0
    with open(abs_local, "rb") as fh:
        first = fh.read(CHUNK)
        session_id = _session_start(first)
        offset = len(first)
        while True:
            data = fh.read(CHUNK)
            if not data:
                break
            _session_append(session_id, offset, data)
            offset += len(data)
    _session_finish(session_id, offset, path)


def _session_start(data):
    arg = {"close": False}
    for attempt in (1, 2):
        headers = _auth_header(force=(attempt == 2))
        headers["Dropbox-API-Arg"] = json.dumps(arg)
        headers["Content-Type"] = "application/octet-stream"
        resp = requests.post(
            CONTENT + "/files/upload_session/start",
            headers=headers,
            data=data,
            timeout=TIMEOUT,
        )
        if resp.status_code == 200:
            return resp.json()["session_id"]
        if attempt == 1 and _handle_retryable(resp):
            continue
        raise DropboxAuthError(
            "upload_session/start failed (HTTP %s)" % resp.status_code
        )


def _session_append(session_id, offset, data):
    arg = {"cursor": {"session_id": session_id, "offset": offset}, "close": False}
    for attempt in (1, 2):
        headers = _auth_header(force=(attempt == 2))
        headers["Dropbox-API-Arg"] = json.dumps(arg)
        headers["Content-Type"] = "application/octet-stream"
        resp = requests.post(
            CONTENT + "/files/upload_session/append_v2",
            headers=headers,
            data=data,
            timeout=TIMEOUT,
        )
        if resp.status_code == 200:
            return
        if attempt == 1 and _handle_retryable(resp):
            continue
        raise DropboxAuthError(
            "upload_session/append_v2 failed (HTTP %s)" % resp.status_code
        )


def _session_finish(session_id, offset, path):
    arg = {
        "cursor": {"session_id": session_id, "offset": offset},
        "commit": {"path": path, "mode": "overwrite", "mute": True},
    }
    for attempt in (1, 2):
        headers = _auth_header(force=(attempt == 2))
        headers["Dropbox-API-Arg"] = json.dumps(arg)
        headers["Content-Type"] = "application/octet-stream"
        resp = requests.post(
            CONTENT + "/files/upload_session/finish",
            headers=headers,
            data=b"",
            timeout=TIMEOUT,
        )
        if resp.status_code == 200:
            return
        if attempt == 1 and _handle_retryable(resp):
            continue
        raise DropboxAuthError(
            "upload_session/finish failed (HTTP %s)" % resp.status_code
        )


# --------------------------------------------------------------------------- listing


def list_backups():
    """Return .zip names in the app folder root, newest first."""
    names = []
    arg = {"path": "", "recursive": False}
    resp = _rpc(API + "/files/list_folder", arg)
    if _handle_retryable(resp):
        resp = _rpc(API + "/files/list_folder", arg, force=True)
    if resp.status_code != 200:
        raise DropboxAuthError("list_folder failed (HTTP %s)" % resp.status_code)
    data = resp.json()
    entries = data.get("entries", [])
    cursor = data.get("cursor")
    has_more = data.get("has_more", False)
    while has_more:
        resp = _rpc(API + "/files/list_folder/continue", {"cursor": cursor})
        if _handle_retryable(resp):
            resp = _rpc(
                API + "/files/list_folder/continue", {"cursor": cursor}, force=True
            )
        if resp.status_code != 200:
            raise DropboxAuthError(
                "list_folder/continue failed (HTTP %s)" % resp.status_code
            )
        data = resp.json()
        entries.extend(data.get("entries", []))
        cursor = data.get("cursor")
        has_more = data.get("has_more", False)

    for e in entries:
        if e.get(".tag") == "file":
            n = e.get("name", "")
            if n.endswith(".zip"):
                names.append(n)
    # filenames carry a _YYYYMMDDHHMM stamp, so a reverse name sort is newest-first
    return sorted(names, reverse=True)


# ------------------------------------------------------------------------- download


def download(remote_name):
    """Download /<remote_name> to special://temp/<remote_name>; return that special:// path."""
    dest_special = "special://temp/" + remote_name
    dest = xbmcvfs.translatePath(dest_special)
    arg = {"path": "/" + remote_name}
    for attempt in (1, 2):
        headers = _auth_header(force=(attempt == 2))
        headers["Dropbox-API-Arg"] = json.dumps(arg)
        resp = requests.post(
            CONTENT + "/files/download",
            headers=headers,
            stream=True,
            timeout=TIMEOUT,
        )
        if resp.status_code == 200:
            with open(dest, "wb") as fh:
                for block in resp.iter_content(chunk_size=1024 * 1024):
                    if block:
                        fh.write(block)
            return dest_special
        if attempt == 1 and _handle_retryable(resp):
            continue
        raise DropboxAuthError("download failed (HTTP %s)" % resp.status_code)


# --------------------------------------------------------------------------- delete


def delete(remote_name):
    arg = {"path": "/" + remote_name}
    resp = _rpc(API + "/files/delete_v2", arg)
    if _handle_retryable(resp):
        resp = _rpc(API + "/files/delete_v2", arg, force=True)
    if resp.status_code != 200:
        raise DropboxAuthError("delete failed (HTTP %s)" % resp.status_code)
    return True
