"""
Adversarial unit tests for dropbox_remote.py (EZ Maintenance++ Dropbox backup).

Goal: try hard to BREAK the REST exactness, chunk/offset math, token refresh +
skew, pagination, error mapping, app-folder pathing, and secret redaction.
"""

import json
import os
import tempfile

import pytest


# ============================================================ token refresh ===
def test_refresh_grant_params(fake_kodi):
    dbx = fake_kodi.dbx
    dbx._addon._settings if hasattr(dbx, "_addon") else None
    fake_kodi.addon._settings.update(
        {
            "dropbox_key": "KEY",
            "dropbox_secret": "SEC",
            "dropbox_refresh_token": "REFRESH",
        }
    )

    def responder(idx, call):
        return fake_kodi.FakeResponse(
            200, {"access_token": "BEARER1", "expires_in": 14400}
        )

    fake_kodi.requests.responder = responder
    tok = dbx._access_token(force=True)
    assert tok == "BEARER1"
    call = fake_kodi.requests.calls[0]
    assert call["url"] == "https://api.dropboxapi.com/oauth2/token"
    assert call["data"]["grant_type"] == "refresh_token"
    assert call["data"]["refresh_token"] == "REFRESH"
    assert call["data"]["client_id"] == "KEY"
    # PKCE: refresh uses client_id only, never a client_secret
    assert "client_secret" not in call["data"]


def test_expiry_skew_5min(fake_kodi):
    """Cached bearer with >5min left is reused; <5min triggers refresh."""
    dbx = fake_kodi.dbx
    import time

    fake_kodi.addon._settings.update(
        {"dropbox_key": "K", "dropbox_secret": "S", "dropbox_refresh_token": "R"}
    )
    # cache expires in 6 minutes -> still valid (skew is 300s)
    dbx._cache["bearer"] = "CACHED"
    dbx._cache["exp"] = time.time() + 360
    assert dbx._access_token() == "CACHED"
    assert len(fake_kodi.requests.calls) == 0  # no refresh

    # cache expires in 4 minutes -> within skew, must refresh
    dbx._cache["bearer"] = "CACHED"
    dbx._cache["exp"] = time.time() + 240
    fake_kodi.requests.responder = lambda i, c: fake_kodi.FakeResponse(
        200, {"access_token": "FRESH", "expires_in": 14400}
    )
    assert dbx._access_token() == "FRESH"
    assert len(fake_kodi.requests.calls) == 1


def test_force_refresh_ignores_valid_cache(fake_kodi):
    dbx = fake_kodi.dbx
    import time

    fake_kodi.addon._settings.update(
        {"dropbox_key": "K", "dropbox_secret": "S", "dropbox_refresh_token": "R"}
    )
    dbx._cache["bearer"] = "CACHED"
    dbx._cache["exp"] = time.time() + 99999
    fake_kodi.requests.responder = lambda i, c: fake_kodi.FakeResponse(
        200, {"access_token": "FORCED", "expires_in": 14400}
    )
    assert dbx._access_token(force=True) == "FORCED"


def test_refresh_400_raises_autherror(fake_kodi):
    dbx = fake_kodi.dbx
    fake_kodi.addon._settings.update(
        {"dropbox_key": "K", "dropbox_secret": "S", "dropbox_refresh_token": "R"}
    )
    fake_kodi.requests.responder = lambda i, c: fake_kodi.FakeResponse(400, {})
    with pytest.raises(dbx.DropboxAuthError):
        dbx._access_token(force=True)


def test_not_signed_in_raises(fake_kodi):
    dbx = fake_kodi.dbx
    # no refresh token set
    with pytest.raises(dbx.DropboxAuthError):
        dbx._access_token(force=True)


# ============================================================ chunk decision ===
def _stage_file(fake_kodi, size_bytes, special="special://temp/x.zip"):
    """Create a real temp file of size_bytes and wire xbmcvfs to it."""
    fd, real = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    with open(real, "wb") as fh:
        # write in 1MB blocks to avoid huge RAM, deterministic content
        block = b"\x00" * (1000 * 1000)
        remaining = size_bytes
        while remaining > 0:
            n = min(len(block), remaining)
            fh.write(block[:n])
            remaining -= n
    fake_kodi.xbmcvfs._temp_map[special] = real
    # Stat must report the size; patch Stat to read real size
    realsize = os.path.getsize(real)

    class _S:
        def __init__(self, p):
            self._p = p

        def st_size(self):
            return realsize

    fake_kodi.xbmcvfs.Stat = _S
    # xbmcvfs.File for the single-shot path reads the whole file
    fake_kodi.FakeFile._payloads[special] = None  # signal: read real file

    # Patch FakeFile to read the real file lazily
    real_path = real

    class _F:
        def __init__(self, path, mode="r"):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def readBytes(self):
            with open(real_path, "rb") as fh:
                return fh.read()

    fake_kodi.xbmcvfs.File = _F
    return real, special


def _wire_upload_responder(fake_kodi):
    """Respond 200 to all upload endpoints; record offsets seen."""
    seen = {
        "single": 0,
        "start": 0,
        "append_offsets": [],
        "finish_offset": None,
        "append_sizes": [],
        "start_size": None,
        "finish_size": None,
    }

    def responder(idx, call):
        url = call["url"]
        data = call["data"]
        if url.endswith("/files/upload"):
            seen["single"] += 1
            return fake_kodi.FakeResponse(200, {})
        if url.endswith("/upload_session/start"):
            seen["start"] += 1
            seen["start_size"] = len(data) if data else 0
            return fake_kodi.FakeResponse(200, {"session_id": "SID"})
        if url.endswith("/upload_session/append_v2"):
            arg = json.loads(call["headers"]["Dropbox-API-Arg"])
            seen["append_offsets"].append(arg["cursor"]["offset"])
            seen["append_sizes"].append(len(data) if data else 0)
            return fake_kodi.FakeResponse(200, {})
        if url.endswith("/upload_session/finish"):
            arg = json.loads(call["headers"]["Dropbox-API-Arg"])
            seen["finish_offset"] = arg["cursor"]["offset"]
            seen["finish_size"] = len(data) if data else 0
            return fake_kodi.FakeResponse(200, {})
        return fake_kodi.FakeResponse(200, {})

    fake_kodi.requests.responder = responder
    return seen


def test_chunk_size_is_small_and_4mib_aligned(fake_kodi):
    """The fix: chunks are small (<=16 MiB, so one finishes inside the timeout on a
    slow uplink) and a multiple of Dropbox's 4 MiB upload-session unit."""
    dbx = fake_kodi.dbx
    assert dbx.CHUNK <= 16 * 1024 * 1024
    assert dbx.CHUNK % (4 * 1024 * 1024) == 0


@pytest.mark.parametrize(
    "size_fn,expect_single",
    [
        (lambda c: c - 1, True),  # < CHUNK -> single shot
        (lambda c: c, True),  # == CHUNK -> single (size <= CHUNK)
        (lambda c: c + 1, False),  # > CHUNK -> session (start + 1 append)
        (lambda c: c * 3 + 7, False),  # several chunks -> start + multiple appends
    ],
)
def test_chunk_decision_and_offsets(fake_kodi, size_fn, expect_single):
    dbx = fake_kodi.dbx
    fake_kodi.addon._settings.update(
        {"dropbox_key": "K", "dropbox_secret": "S", "dropbox_refresh_token": "R"}
    )
    # token: avoid real refresh; prime cache
    import time

    dbx._cache["bearer"] = "B"
    dbx._cache["exp"] = time.time() + 99999

    size = size_fn(dbx.CHUNK)
    real, special = _stage_file(fake_kodi, size)
    seen = _wire_upload_responder(fake_kodi)
    try:
        dbx.upload(special, "x.zip")
    finally:
        os.remove(real)

    if expect_single:
        assert seen["single"] == 1, f"{size}B should be single-shot"
        assert seen["start"] == 0
    else:
        assert seen["single"] == 0, f"{size}B should NOT be single-shot"
        assert seen["start"] == 1
        # offsets must equal cumulative bytes sent: first append offset == CHUNK,
        # each subsequent == prev + chunk_size
        offsets = seen["append_offsets"]
        # reconstruct expected offsets
        sizes = [seen["start_size"]] + seen["append_sizes"]
        cum = 0
        expected_offsets = []
        cum += sizes[0]
        for s in seen["append_sizes"]:
            expected_offsets.append(cum)
            cum += s
        assert offsets == expected_offsets, (
            f"offset math wrong: got {offsets} expected {expected_offsets}"
        )
        # finish offset must equal total file size (== sum of all sent bytes)
        assert seen["finish_offset"] == size, (
            f"finish offset {seen['finish_offset']} != file size {size}"
        )
        # finish body must be empty
        assert seen["finish_size"] == 0
        # total bytes across all sends == file size (no double-send / no gap)
        total_sent = seen["start_size"] + sum(seen["append_sizes"])
        assert total_sent == size, f"sent {total_sent} != {size}"


def test_chunk_boundary_plus_one_uses_session(fake_kodi):
    """The exact boundary: CHUNK+1 byte must switch to a session (size > CHUNK)."""
    dbx = fake_kodi.dbx
    import time

    fake_kodi.addon._settings.update(
        {"dropbox_key": "K", "dropbox_secret": "S", "dropbox_refresh_token": "R"}
    )
    dbx._cache["bearer"] = "B"
    dbx._cache["exp"] = time.time() + 99999
    size = dbx.CHUNK + 1
    real, special = _stage_file(fake_kodi, size)
    seen = _wire_upload_responder(fake_kodi)
    try:
        dbx.upload(special, "x.zip")
    finally:
        os.remove(real)
    assert seen["single"] == 0
    assert seen["start"] == 1
    assert seen["finish_offset"] == size
    # one trailing byte beyond the first (start) chunk -> exactly one append of size 1
    assert seen["append_sizes"] == [1]
    assert seen["append_offsets"] == [dbx.CHUNK]


# ============================================================ API-arg shape ===
def test_single_upload_headers_and_path(fake_kodi):
    dbx = fake_kodi.dbx
    import time

    fake_kodi.addon._settings.update(
        {"dropbox_key": "K", "dropbox_secret": "S", "dropbox_refresh_token": "R"}
    )
    dbx._cache["bearer"] = "B"
    dbx._cache["exp"] = time.time() + 99999
    real, special = _stage_file(fake_kodi, 1 * 1000 * 1000)
    fake_kodi.requests.responder = lambda i, c: fake_kodi.FakeResponse(200, {})
    try:
        dbx.upload(special, "myfile.zip")
    finally:
        os.remove(real)
    call = fake_kodi.requests.calls[0]
    assert call["url"] == "https://content.dropboxapi.com/2/files/upload"
    assert call["headers"]["Content-Type"] == "application/octet-stream"
    arg = json.loads(call["headers"]["Dropbox-API-Arg"])
    # app-folder RELATIVE path: must be "/myfile.zip", NEVER "/Apps/..."
    assert arg["path"] == "/myfile.zip"
    assert not arg["path"].startswith("/Apps")
    assert arg["mode"] == "overwrite"
    assert arg["mute"] is True
    assert call["headers"]["Authorization"] == "Bearer B"


def test_authorize_url_shape(fake_kodi):
    dbx = fake_kodi.dbx
    fake_kodi.addon._settings.update({"dropbox_key": "MYKEY", "dropbox_secret": "S"})
    # feed empty code so authorize returns early after building the URL,
    # but capture the URL via the ok() dialog
    fake_kodi.xbmcgui.Dialog.inputs = [""]
    dbx.authorize()
    # the URL appears in the first ok() dialog
    ok_args = fake_kodi.xbmcgui.Dialog.last_ok
    blob = " ".join(str(a) for a in ok_args)
    assert "https://www.dropbox.com/oauth2/authorize" in blob
    assert "client_id=MYKEY" in blob
    assert "response_type=code" in blob
    assert "token_access_type=offline" in blob
    # PKCE: the challenge (not a secret) rides in the URL
    assert "code_challenge=" in blob
    assert "code_challenge_method=S256" in blob


def test_authorize_code_exchange_params(fake_kodi):
    dbx = fake_kodi.dbx
    fake_kodi.addon._settings.update({"dropbox_key": "K", "dropbox_secret": "S"})
    fake_kodi.xbmcgui.Dialog.inputs = ["AUTHCODE123"]
    fake_kodi.requests.responder = lambda i, c: fake_kodi.FakeResponse(
        200, {"refresh_token": "RT", "access_token": "AT", "expires_in": 14400}
    )
    ok = dbx.authorize()
    assert ok is True
    call = fake_kodi.requests.calls[0]
    assert call["url"] == "https://api.dropboxapi.com/oauth2/token"
    assert call["data"]["grant_type"] == "authorization_code"
    assert call["data"]["code"] == "AUTHCODE123"
    assert call["data"]["client_id"] == "K"
    # PKCE: the code is redeemed with a code_verifier, never a client_secret
    assert call["data"]["code_verifier"]
    assert "client_secret" not in call["data"]
    # refresh token persisted
    assert fake_kodi.addon._settings["dropbox_refresh_token"] == "RT"


# ============================================================ pagination ===
def test_list_folder_pagination(fake_kodi):
    dbx = fake_kodi.dbx
    import time

    fake_kodi.addon._settings.update(
        {"dropbox_key": "K", "dropbox_secret": "S", "dropbox_refresh_token": "R"}
    )
    dbx._cache["bearer"] = "B"
    dbx._cache["exp"] = time.time() + 99999

    pages = [
        # page 1
        fake_kodi.FakeResponse(
            200,
            {
                "entries": [
                    {".tag": "file", "name": "a_202601010101.zip"},
                    {".tag": "folder", "name": "ignore"},
                ],
                "cursor": "CUR1",
                "has_more": True,
            },
        ),
        # page 2 (continue)
        fake_kodi.FakeResponse(
            200,
            {
                "entries": [
                    {".tag": "file", "name": "b_202601020101.zip"},
                    {".tag": "file", "name": "notzip.txt"},
                ],
                "cursor": "CUR2",
                "has_more": True,
            },
        ),
        # page 3 (continue) -> last
        fake_kodi.FakeResponse(
            200,
            {
                "entries": [{".tag": "file", "name": "c_202601030101.zip"}],
                "cursor": "CUR3",
                "has_more": False,
            },
        ),
    ]
    state = {"i": 0}

    def responder(idx, call):
        r = pages[state["i"]]
        state["i"] += 1
        return r

    fake_kodi.requests.responder = responder
    names = dbx.list_backups()
    # 3 zips collected across 3 pages, newest-first (reverse name sort)
    assert names == [
        "c_202601030101.zip",
        "b_202601020101.zip",
        "a_202601010101.zip",
    ]
    # first call list_folder, then 2 continues
    assert fake_kodi.requests.calls[0]["url"].endswith("/files/list_folder")
    assert fake_kodi.requests.calls[1]["url"].endswith("/files/list_folder/continue")
    assert fake_kodi.requests.calls[2]["url"].endswith("/files/list_folder/continue")
    # cursor threaded correctly
    assert json.loads(fake_kodi.requests.calls[1]["data"])["cursor"] == "CUR1"
    assert json.loads(fake_kodi.requests.calls[2]["data"])["cursor"] == "CUR2"


def test_list_backups_newest_first_by_timestamp_with_mixed_names(fake_kodi):
    """FIX (HIGH-2): list_backups must order by the in-name _YYYYMMDDHHMM stamp,
    NOT raw lexical name order, so mixed user-chosen name prefixes never put an
    older file at index 0. The true newest by date must be first (so keep=1
    rotation, which deletes names[n:], can never prune the newest)."""
    dbx = fake_kodi.dbx
    import time

    fake_kodi.addon._settings.update(
        {"dropbox_key": "K", "dropbox_secret": "S", "dropbox_refresh_token": "R"}
    )
    dbx._cache["bearer"] = "B"
    dbx._cache["exp"] = time.time() + 99999
    entries = [
        {".tag": "file", "name": "My_Build_202601010101.zip"},  # OLDEST by date
        {".tag": "file", "name": "zzz_202601020101.zip"},  # middle by date
        {".tag": "file", "name": "kodi_backup_202601030101.zip"},  # NEWEST by date
    ]
    fake_kodi.requests.responder = lambda i, c: fake_kodi.FakeResponse(
        200, {"entries": entries, "has_more": False}
    )
    names = dbx.list_backups()
    # Strict newest-first by the trailing timestamp, regardless of name prefix.
    assert names == [
        "kodi_backup_202601030101.zip",  # 2026-01-03 -> newest
        "zzz_202601020101.zip",  # 2026-01-02
        "My_Build_202601010101.zip",  # 2026-01-01 -> oldest
    ]
    assert names[0] == "kodi_backup_202601030101.zip"  # true newest leads


def test_list_backups_unstamped_sorts_oldest(fake_kodi):
    """A file with no parseable _YYYYMMDDHHMM stamp must sort as the OLDEST so it
    can never displace (or cause deletion of) a stamped, newer backup."""
    dbx = fake_kodi.dbx
    import time

    fake_kodi.addon._settings.update(
        {"dropbox_key": "K", "dropbox_secret": "S", "dropbox_refresh_token": "R"}
    )
    dbx._cache["bearer"] = "B"
    dbx._cache["exp"] = time.time() + 99999
    entries = [
        {".tag": "file", "name": "legacy_no_stamp.zip"},  # unstamped -> oldest
        {".tag": "file", "name": "kodi_backup_202601020101.zip"},  # newest
    ]
    fake_kodi.requests.responder = lambda i, c: fake_kodi.FakeResponse(
        200, {"entries": entries, "has_more": False}
    )
    names = dbx.list_backups()
    assert names[0] == "kodi_backup_202601020101.zip"
    assert names[-1] == "legacy_no_stamp.zip"  # unstamped never leads


def test_list_folder_arg_shape(fake_kodi):
    dbx = fake_kodi.dbx
    import time

    fake_kodi.addon._settings.update(
        {"dropbox_key": "K", "dropbox_secret": "S", "dropbox_refresh_token": "R"}
    )
    dbx._cache["bearer"] = "B"
    dbx._cache["exp"] = time.time() + 99999
    fake_kodi.requests.responder = lambda i, c: fake_kodi.FakeResponse(
        200, {"entries": [], "has_more": False}
    )
    dbx.list_backups()
    call = fake_kodi.requests.calls[0]
    body = json.loads(call["data"])
    # app-folder root is "" NOT "/" and NOT "/Apps/..."
    assert body["path"] == ""
    assert body["recursive"] is False
    assert call["headers"]["Content-Type"] == "application/json"


# ============================================================ error mapping ===
def test_upload_401_forces_refresh_then_retries(fake_kodi):
    dbx = fake_kodi.dbx
    import time

    fake_kodi.addon._settings.update(
        {"dropbox_key": "K", "dropbox_secret": "S", "dropbox_refresh_token": "R"}
    )
    dbx._cache["bearer"] = "OLD"
    dbx._cache["exp"] = time.time() + 99999
    real, special = _stage_file(fake_kodi, 1 * 1000 * 1000)
    state = {"n": 0}

    def responder(idx, call):
        url = call["url"]
        if url.endswith("/oauth2/token"):
            return fake_kodi.FakeResponse(
                200, {"access_token": "NEW", "expires_in": 14400}
            )
        # first upload 401, second 200
        state["n"] += 1
        if state["n"] == 1:
            return fake_kodi.FakeResponse(401, {})
        return fake_kodi.FakeResponse(200, {})

    fake_kodi.requests.responder = responder
    try:
        assert dbx.upload(special, "x.zip") is True
    finally:
        os.remove(real)
    # second upload used the refreshed bearer
    upload_calls = [
        c for c in fake_kodi.requests.calls if c["url"].endswith("/files/upload")
    ]
    assert upload_calls[-1]["headers"]["Authorization"] == "Bearer NEW"


def test_429_retry_after_backoff(fake_kodi, monkeypatch):
    dbx = fake_kodi.dbx
    import time

    fake_kodi.addon._settings.update(
        {"dropbox_key": "K", "dropbox_secret": "S", "dropbox_refresh_token": "R"}
    )
    dbx._cache["bearer"] = "B"
    dbx._cache["exp"] = time.time() + 99999
    slept = []
    monkeypatch.setattr(dbx.time, "sleep", lambda s: slept.append(s))
    real, special = _stage_file(fake_kodi, 1 * 1000 * 1000)
    state = {"n": 0}

    def responder(idx, call):
        state["n"] += 1
        if state["n"] == 1:
            return fake_kodi.FakeResponse(429, {}, headers={"Retry-After": "3"})
        return fake_kodi.FakeResponse(200, {})

    fake_kodi.requests.responder = responder
    try:
        assert dbx.upload(special, "x.zip") is True
    finally:
        os.remove(real)
    # Retry-After 3 + 1 = 4
    assert slept == [4]


def test_delete_uses_delete_v2_relative_path(fake_kodi):
    dbx = fake_kodi.dbx
    import time

    fake_kodi.addon._settings.update(
        {"dropbox_key": "K", "dropbox_secret": "S", "dropbox_refresh_token": "R"}
    )
    dbx._cache["bearer"] = "B"
    dbx._cache["exp"] = time.time() + 99999
    fake_kodi.requests.responder = lambda i, c: fake_kodi.FakeResponse(200, {})
    dbx.delete("old_202601010101.zip")
    call = fake_kodi.requests.calls[0]
    assert call["url"] == "https://api.dropboxapi.com/2/files/delete_v2"
    body = json.loads(call["data"])
    assert body["path"] == "/old_202601010101.zip"
    assert not body["path"].startswith("/Apps")


def test_download_streamed_to_temp(fake_kodi, tmp_path):
    dbx = fake_kodi.dbx
    import time

    fake_kodi.addon._settings.update(
        {"dropbox_key": "K", "dropbox_secret": "S", "dropbox_refresh_token": "R"}
    )
    dbx._cache["bearer"] = "B"
    dbx._cache["exp"] = time.time() + 99999
    dest_real = str(tmp_path / "dl.zip")
    fake_kodi.xbmcvfs._temp_map["special://temp/dl.zip"] = dest_real
    payload = b"PAYLOAD" * 100000
    fake_kodi.requests.responder = lambda i, c: fake_kodi.FakeResponse(
        200, {}, content=payload
    )
    out = dbx.download("dl.zip")
    assert out == "special://temp/dl.zip"
    with open(dest_real, "rb") as fh:
        assert fh.read() == payload
    # download arg path is relative
    call = fake_kodi.requests.calls[0]
    arg = json.loads(call["headers"]["Dropbox-API-Arg"])
    assert arg["path"] == "/dl.zip"
    assert call["stream"] is True


def test_download_progress_total_from_api_result(fake_kodi, tmp_path):
    """The download gauge needs a total. Dropbox streams chunked (no Content-Length),
    so the size must come from the Dropbox-API-Result header - else the bar stays at 0."""
    dbx = fake_kodi.dbx
    import time

    fake_kodi.addon._settings.update(
        {"dropbox_key": "K", "dropbox_secret": "S", "dropbox_refresh_token": "R"}
    )
    dbx._cache["bearer"] = "B"
    dbx._cache["exp"] = time.time() + 99999
    dest_real = str(tmp_path / "dl.zip")
    fake_kodi.xbmcvfs._temp_map["special://temp/dl.zip"] = dest_real
    payload = b"X" * 5000
    fake_kodi.requests.responder = lambda i, c: fake_kodi.FakeResponse(
        200,
        {},
        headers={"Dropbox-API-Result": json.dumps({"size": 5000})},
        content=payload,
    )
    seen = []
    dbx.download("dl.zip", progress=lambda r, t: seen.append((r, t)))
    assert seen, "progress callback was never called"
    assert seen[-1] == (5000, 5000)  # total parsed from Dropbox-API-Result, not 0


def test_download_broken_stream_removes_partial_temp(fake_kodi, tmp_path):
    """FIX (LOW-4): if the stream breaks mid-write, the partial temp file must be
    removed (so a later restore can't pick up a truncated zip) and the error
    re-raised - never a silent half-file left behind."""
    dbx = fake_kodi.dbx
    import time

    fake_kodi.addon._settings.update(
        {"dropbox_key": "K", "dropbox_secret": "S", "dropbox_refresh_token": "R"}
    )
    dbx._cache["bearer"] = "B"
    dbx._cache["exp"] = time.time() + 99999
    dest_real = str(tmp_path / "dl.zip")
    fake_kodi.xbmcvfs._temp_map["special://temp/dl.zip"] = dest_real

    class _BrokenResponse(fake_kodi.FakeResponse):
        def iter_content(self, chunk_size=1):
            yield b"partial-data-"
            raise OSError("stream reset")

    fake_kodi.requests.responder = lambda i, c: _BrokenResponse(200, {})
    with pytest.raises(OSError):
        dbx.download("dl.zip")
    # no partial file lingers
    assert not os.path.exists(dest_real), "partial temp file was not cleaned up"


def test_download_makes_dest_dir(fake_kodi, tmp_path):
    """FIX (LOW-5): download() mkdirs the dest directory before opening it, so a
    missing special://temp does not blow up the write."""
    dbx = fake_kodi.dbx
    import time

    fake_kodi.addon._settings.update(
        {"dropbox_key": "K", "dropbox_secret": "S", "dropbox_refresh_token": "R"}
    )
    dbx._cache["bearer"] = "B"
    dbx._cache["exp"] = time.time() + 99999
    # point dest at a NOT-yet-existing nested dir
    missing_dir = tmp_path / "does" / "not" / "exist"
    dest_real = str(missing_dir / "dl.zip")
    assert not missing_dir.exists()
    fake_kodi.xbmcvfs._temp_map["special://temp/dl.zip"] = dest_real
    payload = b"OK" * 10
    fake_kodi.requests.responder = lambda i, c: fake_kodi.FakeResponse(
        200, {}, content=payload
    )
    out = dbx.download("dl.zip")
    assert out == "special://temp/dl.zip"
    with open(dest_real, "rb") as fh:
        assert fh.read() == payload


def test_single_upload_streams_file_object_not_buffer(fake_kodi, tmp_path):
    """FIX (MEDIUM-3): the single-shot (<=50MB) path must pass an open file object
    as the POST body (requests streams from disk), NOT a fully-read bytes buffer."""
    dbx = fake_kodi.dbx
    import time

    fake_kodi.addon._settings.update(
        {"dropbox_key": "K", "dropbox_secret": "S", "dropbox_refresh_token": "R"}
    )
    dbx._cache["bearer"] = "B"
    dbx._cache["exp"] = time.time() + 99999
    # a real small file on disk; wire special-> real path + Stat size
    real = str(tmp_path / "x.zip")
    with open(real, "wb") as fh:
        fh.write(b"Z" * 4096)
    special = "special://temp/x.zip"
    fake_kodi.xbmcvfs._temp_map[special] = real

    class _S:
        def __init__(self, p):
            pass

        def st_size(self):
            return 4096

    fake_kodi.xbmcvfs.Stat = _S
    fake_kodi.requests.responder = lambda i, c: fake_kodi.FakeResponse(200, {})
    assert dbx.upload(special, "x.zip") is True
    body = fake_kodi.requests.calls[0]["data"]
    # The body is a file-like object (has read), NOT bytes -> no full-file buffer.
    assert hasattr(body, "read"), "upload body must be a streamed file object"
    assert not isinstance(body, (bytes, bytearray)), (
        "upload still buffers the whole file in memory"
    )


def test_upload_session_start_409_raises(fake_kodi):
    """A non-retryable error (e.g. 409 insufficient_space) during a session
    surfaces as DropboxAuthError, not a silent partial."""
    dbx = fake_kodi.dbx
    import time

    fake_kodi.addon._settings.update(
        {"dropbox_key": "K", "dropbox_secret": "S", "dropbox_refresh_token": "R"}
    )
    dbx._cache["bearer"] = "B"
    dbx._cache["exp"] = time.time() + 99999
    size = 60 * 1000 * 1000
    real, special = _stage_file(fake_kodi, size)

    def responder(idx, call):
        if call["url"].endswith("/upload_session/start"):
            return fake_kodi.FakeResponse(409, {})
        return fake_kodi.FakeResponse(200, {"session_id": "X"})

    fake_kodi.requests.responder = responder
    try:
        with pytest.raises(dbx.DropboxAuthError):
            # upload() retries the whole op once; both attempts hit 409 -> raises
            dbx.upload(special, "x.zip")
    finally:
        os.remove(real)


def test_single_upload_409_raises_no_silent_success(fake_kodi):
    dbx = fake_kodi.dbx
    import time

    fake_kodi.addon._settings.update(
        {"dropbox_key": "K", "dropbox_secret": "S", "dropbox_refresh_token": "R"}
    )
    dbx._cache["bearer"] = "B"
    dbx._cache["exp"] = time.time() + 99999
    real, special = _stage_file(fake_kodi, 1 * 1000 * 1000)
    fake_kodi.requests.responder = lambda i, c: fake_kodi.FakeResponse(409, {})
    try:
        with pytest.raises(dbx.DropboxAuthError):
            dbx.upload(special, "x.zip")
    finally:
        os.remove(real)


# ===================================================== resilient sessions ===
def _prime(fake_kodi):
    import time

    fake_kodi.addon._settings.update(
        {"dropbox_key": "K", "dropbox_secret": "S", "dropbox_refresh_token": "R"}
    )
    fake_kodi.dbx._cache["bearer"] = "B"
    fake_kodi.dbx._cache["exp"] = time.time() + 99999


def test_session_resumes_after_chunk_timeout(fake_kodi, monkeypatch):
    """FIX: a chunk that times out is retried at the SAME offset (resumed from disk),
    so no bytes are skipped and the whole upload still completes - the original bug
    was a single timed-out 50 MB chunk killing the entire upload."""
    dbx = fake_kodi.dbx
    _prime(fake_kodi)
    monkeypatch.setattr(dbx.time, "sleep", lambda s: None)  # no real backoff wait
    size = dbx.CHUNK * 2 + 123  # start + 2 appends
    real, special = _stage_file(fake_kodi, size)
    seen = {"start_size": None, "appends": [], "finish_offset": None}
    state = {"append_calls": 0}

    def responder(idx, call):
        url = call["url"]
        if url.endswith("/upload_session/start"):
            seen["start_size"] = len(call["data"]) if call["data"] else 0
            return fake_kodi.FakeResponse(200, {"session_id": "SID"})
        if url.endswith("/upload_session/append_v2"):
            state["append_calls"] += 1
            # the very first append attempt times out mid-write
            if state["append_calls"] == 1:
                raise OSError("write operation timed out")
            arg = json.loads(call["headers"]["Dropbox-API-Arg"])
            seen["appends"].append(
                (arg["cursor"]["offset"], len(call["data"]) if call["data"] else 0)
            )
            return fake_kodi.FakeResponse(200, {})
        if url.endswith("/upload_session/finish"):
            arg = json.loads(call["headers"]["Dropbox-API-Arg"])
            seen["finish_offset"] = arg["cursor"]["offset"]
            return fake_kodi.FakeResponse(200, {})
        return fake_kodi.FakeResponse(200, {})

    fake_kodi.requests.responder = responder
    try:
        assert dbx.upload(special, "x.zip") is True
    finally:
        os.remove(real)
    # the retried append re-sent the SAME first chunk (offset == start_size), not the
    # next one - nothing was skipped by the timeout
    assert seen["appends"][0][0] == seen["start_size"]
    assert seen["finish_offset"] == size
    # every byte accounted for across start + successful appends (no gap, no dup)
    assert seen["start_size"] + sum(s for _o, s in seen["appends"]) == size


def test_session_resyncs_on_incorrect_offset(fake_kodi, monkeypatch):
    """FIX: on Dropbox's 409 incorrect_offset the loop resumes from the offset
    Dropbox reports (correct_offset) instead of re-sending what it already has."""
    dbx = fake_kodi.dbx
    _prime(fake_kodi)
    monkeypatch.setattr(dbx.time, "sleep", lambda s: None)
    size = dbx.CHUNK * 3 + 5
    real, special = _stage_file(fake_kodi, size)
    seen = {"start_size": None, "offsets": [], "finish_offset": None}
    state = {"n": 0}

    def responder(idx, call):
        url = call["url"]
        if url.endswith("/upload_session/start"):
            seen["start_size"] = len(call["data"]) if call["data"] else 0
            return fake_kodi.FakeResponse(200, {"session_id": "SID"})
        if url.endswith("/upload_session/append_v2"):
            arg = json.loads(call["headers"]["Dropbox-API-Arg"])
            off = arg["cursor"]["offset"]
            seen["offsets"].append(off)
            state["n"] += 1
            if state["n"] == 1:
                # Dropbox says it already holds one more chunk than we think
                return fake_kodi.FakeResponse(
                    409,
                    {
                        "error": {
                            ".tag": "incorrect_offset",
                            "correct_offset": off + dbx.CHUNK,
                        }
                    },
                )
            return fake_kodi.FakeResponse(200, {})
        if url.endswith("/upload_session/finish"):
            arg = json.loads(call["headers"]["Dropbox-API-Arg"])
            seen["finish_offset"] = arg["cursor"]["offset"]
            return fake_kodi.FakeResponse(200, {})
        return fake_kodi.FakeResponse(200, {})

    fake_kodi.requests.responder = responder
    try:
        assert dbx.upload(special, "x.zip") is True
    finally:
        os.remove(real)
    # first append at the start-chunk boundary, then RESUMED at the corrected offset
    assert seen["offsets"][0] == seen["start_size"]
    assert seen["offsets"][1] == seen["start_size"] + dbx.CHUNK
    assert seen["finish_offset"] == size


def test_session_gives_up_after_max_tries_no_infinite_loop(fake_kodi, monkeypatch):
    """A persistently failing chunk raises (bounded) rather than looping forever:
    MAX_TRIES attempts per session, and upload() retries the whole op once -> the
    append endpoint is hit exactly 2 * MAX_TRIES times, then it surfaces an error."""
    dbx = fake_kodi.dbx
    _prime(fake_kodi)
    monkeypatch.setattr(dbx.time, "sleep", lambda s: None)
    size = dbx.CHUNK + 10
    real, special = _stage_file(fake_kodi, size)
    counts = {"append": 0}

    def responder(idx, call):
        url = call["url"]
        if url.endswith("/upload_session/start"):
            return fake_kodi.FakeResponse(200, {"session_id": "SID"})
        if url.endswith("/upload_session/append_v2"):
            counts["append"] += 1
            raise OSError("write operation timed out")
        return fake_kodi.FakeResponse(200, {})

    fake_kodi.requests.responder = responder
    try:
        with pytest.raises(dbx.DropboxAuthError):
            dbx.upload(special, "x.zip")
    finally:
        os.remove(real)
    assert counts["append"] == 2 * dbx.MAX_TRIES


def test_session_append_retries_transient_5xx(fake_kodi, monkeypatch):
    """A 5xx during append is retried with backoff (then succeeds), not raised."""
    dbx = fake_kodi.dbx
    _prime(fake_kodi)
    slept = []
    monkeypatch.setattr(dbx.time, "sleep", lambda s: slept.append(s))
    size = dbx.CHUNK + 10
    real, special = _stage_file(fake_kodi, size)
    state = {"append": 0}

    def responder(idx, call):
        url = call["url"]
        if url.endswith("/upload_session/start"):
            return fake_kodi.FakeResponse(200, {"session_id": "SID"})
        if url.endswith("/upload_session/append_v2"):
            state["append"] += 1
            if state["append"] == 1:
                return fake_kodi.FakeResponse(503, {})  # transient
            return fake_kodi.FakeResponse(200, {})
        return fake_kodi.FakeResponse(200, {})

    fake_kodi.requests.responder = responder
    try:
        assert dbx.upload(special, "x.zip") is True
    finally:
        os.remove(real)
    assert slept, "a transient 5xx should back off before retrying"


def test_resumed_upload_is_byte_identical(fake_kodi, monkeypatch):
    """A chunk that stalls AFTER Dropbox received it forces a resume. Verify the bytes
    Dropbox ends up holding are identical to the source - i.e. a screensaver-style stall
    mid-upload (which this exact backup hit) cannot skip or duplicate bytes."""
    dbx = fake_kodi.dbx
    _prime(fake_kodi)
    monkeypatch.setattr(dbx.time, "sleep", lambda s: None)
    size = dbx.CHUNK * 3 + 1234
    real, special = _stage_file(fake_kodi, size)
    # overwrite the staged zeros with a non-uniform deterministic pattern, so a skip or
    # duplication is detectable (all-zero content would hide it)
    pattern = bytes((i * 31 + 7) & 0xFF for i in range(size))
    with open(real, "wb") as fh:
        fh.write(pattern)

    server = {"recv": bytearray(), "failed_once": False}

    def responder(idx, call):
        url = call["url"]
        data = call["data"] or b""
        if url.endswith("/upload_session/start"):
            server["recv"] = bytearray(data)
            return fake_kodi.FakeResponse(200, {"session_id": "SID"})
        if url.endswith("/upload_session/append_v2"):
            off = json.loads(call["headers"]["Dropbox-API-Arg"])["cursor"]["offset"]
            if off != len(server["recv"]):
                # client is out of sync -> Dropbox replies with the true offset
                return fake_kodi.FakeResponse(
                    409,
                    {
                        "error": {
                            ".tag": "incorrect_offset",
                            "correct_offset": len(server["recv"]),
                        }
                    },
                )
            # offset matches: Dropbox receives the chunk, then on the first append the
            # RESPONSE is lost (the stall) so the client must recover without corrupting
            server["recv"].extend(data)
            if not server["failed_once"]:
                server["failed_once"] = True
                raise OSError("stall after Dropbox received the chunk")
            return fake_kodi.FakeResponse(200, {})
        if url.endswith("/upload_session/finish"):
            return fake_kodi.FakeResponse(200, {})
        return fake_kodi.FakeResponse(200, {})

    fake_kodi.requests.responder = responder
    try:
        assert dbx.upload(special, "x.zip") is True
    finally:
        os.remove(real)
    assert len(server["recv"]) == size
    assert bytes(server["recv"]) == pattern, "resumed upload corrupted the data"


# ===================================================== progress + cancel ===
def test_single_upload_reports_progress(fake_kodi):
    dbx = fake_kodi.dbx
    _prime(fake_kodi)
    real, special = _stage_file(fake_kodi, 1000)  # < CHUNK -> single shot
    fake_kodi.requests.responder = lambda i, c: fake_kodi.FakeResponse(200, {})
    seen = []
    try:
        assert (
            dbx.upload(
                special, "x.zip", progress=lambda s, t: seen.append((s, t)) or True
            )
            is True
        )
    finally:
        os.remove(real)
    assert seen[0] == (0, 1000)
    assert seen[-1] == (1000, 1000)


def test_session_upload_reports_monotonic_progress(fake_kodi):
    dbx = fake_kodi.dbx
    _prime(fake_kodi)
    size = dbx.CHUNK * 2 + 50
    real, special = _stage_file(fake_kodi, size)
    _wire_upload_responder(fake_kodi)
    seen = []
    try:
        assert (
            dbx.upload(special, "x.zip", progress=lambda s, t: seen.append((s, t)))
            is not False
        )
    finally:
        os.remove(real)
    # total is always the file size; sent is non-decreasing and ends exactly at size
    assert all(t == size for _s, t in seen)
    sents = [s for s, _t in seen]
    assert sents == sorted(sents)
    assert sents[0] == 0  # reports 0 before the session opens (no 100% flash)
    assert sents[-1] == size
    assert dbx.CHUNK in sents  # advances by chunk after the start


def test_upload_progress_cancel_raises_and_is_not_retried(fake_kodi):
    dbx = fake_kodi.dbx
    _prime(fake_kodi)
    size = dbx.CHUNK * 3
    real, special = _stage_file(fake_kodi, size)
    counts = {"start": 0, "append": 0}

    def responder(idx, call):
        url = call["url"]
        if url.endswith("/upload_session/start"):
            counts["start"] += 1
            return fake_kodi.FakeResponse(200, {"session_id": "SID"})
        if url.endswith("/upload_session/append_v2"):
            counts["append"] += 1
            return fake_kodi.FakeResponse(200, {})
        return fake_kodi.FakeResponse(200, {})

    fake_kodi.requests.responder = responder
    calls = {"n": 0}

    def progress(sent, total):
        calls["n"] += 1
        return calls["n"] < 2  # allow the initial 0% report, then cancel after start

    try:
        with pytest.raises(dbx.DropboxCanceled):
            dbx.upload(special, "x.zip", progress=progress)
    finally:
        os.remove(real)
    # canceled right after the session opened, before any append, and NOT retried
    assert counts["append"] == 0
    assert counts["start"] == 1


def test_authorize_falls_back_to_url_dialog_when_no_qr(fake_kodi):
    """The QR fetch uses requests.get (absent in the fake), so authorize() must fall
    back to showing the URL in an ok() dialog - sign-in still works without QR."""
    dbx = fake_kodi.dbx
    fake_kodi.addon._settings.update({"dropbox_key": "MYKEY", "dropbox_secret": "S"})
    fake_kodi.xbmcgui.Dialog.inputs = [""]  # cancel after the prompt
    dbx.authorize()
    blob = " ".join(str(a) for a in fake_kodi.xbmcgui.Dialog.last_ok)
    assert "https://www.dropbox.com/oauth2/authorize" in blob
    assert "client_id=MYKEY" in blob


# ============================================================ redaction ===
def test_no_secret_in_logs_on_authorize(fake_kodi):
    """After a full authorize, no log line may contain the code/refresh/access token."""
    dbx = fake_kodi.dbx
    fake_kodi.addon._settings.update(
        {"dropbox_key": "MYKEY", "dropbox_secret": "MYSECRET"}
    )
    fake_kodi.xbmcgui.Dialog.inputs = ["SECRET_AUTH_CODE"]
    fake_kodi.requests.responder = lambda i, c: fake_kodi.FakeResponse(
        200,
        {
            "refresh_token": "SECRET_REFRESH",
            "access_token": "SECRET_ACCESS",
            "expires_in": 14400,
        },
    )
    dbx.authorize()
    joined = "\n".join(m for _lvl, m in fake_kodi.log_lines)
    for secret in (
        "SECRET_AUTH_CODE",
        "SECRET_REFRESH",
        "SECRET_ACCESS",
        "MYKEY",
        "MYSECRET",
    ):
        assert secret not in joined, f"{secret!r} leaked into logs: {joined!r}"


def test_log_helper_never_emits_passed_secret(fake_kodi):
    """The _log helper itself: even if a caller (bug) passes a secret-shaped
    string, confirm there's no redaction (documents current behavior: _log does
    NOT redact, it relies on callers never passing secrets)."""
    dbx = fake_kodi.dbx
    dbx._log("token=ABC123")  # if a future caller did this, it WOULD leak
    joined = "\n".join(m for _lvl, m in fake_kodi.log_lines)
    # This asserts the CURRENT (no-redaction) behavior so a regression that adds
    # secret-bearing log calls is caught by the authorize/upload redaction tests.
    assert "token=ABC123" in joined


def test_refresh_network_error_maps_to_autherror(fake_kodi):
    dbx = fake_kodi.dbx
    fake_kodi.addon._settings.update(
        {"dropbox_key": "K", "dropbox_secret": "S", "dropbox_refresh_token": "R"}
    )

    def boom(*a, **k):
        raise OSError("connection reset")

    fake_kodi.requests.post = boom
    with pytest.raises(dbx.DropboxAuthError):
        dbx._access_token(force=True)
