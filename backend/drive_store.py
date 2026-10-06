"""Google Drive storage for generated media and metadata.

Render's free disk is ephemeral — anything written at runtime is gone on the
next restart, and free instances restart constantly. Drive gives the generated
artefacts somewhere durable to live.

Uses OAuth delegation (your own account's quota) rather than a service account:
service accounts have no Drive storage of their own and can't create files even
inside a folder shared with them, which fails with a quota error on personal
Gmail accounts.

Entirely optional — with the env vars unset, every call is a no-op and the app
behaves exactly as before.
"""

import http.client
import io
import json
import mimetypes
import os
import threading
from pathlib import Path
from typing import Optional

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
TOKEN_URI = "https://oauth2.googleapis.com/token"
FOLDER_MIME = "application/vnd.google-apps.folder"

ROOT_FOLDER_ID = os.environ.get("GOOGLE_DRIVE_ROOT_FOLDER_ID", "").strip()
OAUTH_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip()
OAUTH_CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
OAUTH_REFRESH_TOKEN = os.environ.get("GOOGLE_OAUTH_REFRESH_TOKEN", "").strip()

# The Drive client is explicitly NOT thread-safe: a Resource wraps a single
# httplib2 connection, so two threads calling .execute() on the same one write
# into the same socket and each reads back part of the other's TLS stream. That
# surfaces as "[SSL: WRONG_VERSION_NUMBER] wrong version number", which reads
# like a TLS misconfiguration and is really just two threads sharing a pipe.
#
# FastAPI runs every sync endpoint in a threadpool, and the scheduler and
# pipeline have threads of their own, so concurrent Drive calls are the normal
# case here, not an edge case. Each thread therefore gets its own client.
_local = threading.local()

# Still shared, so still guarded: the folder-id cache, and folder creation,
# where two threads racing would make two folders of the same name.
_lock = threading.Lock()
_folder_cache: dict[str, str] = {}

# A dead or half-open socket raises from the transport rather than from the
# API. ssl.SSLError, ConnectionError, BrokenPipeError and socket.timeout are
# all OSError subclasses, so this pair covers them.
TRANSPORT_ERRORS = (OSError, http.client.HTTPException)


class DriveError(RuntimeError):
    """Drive was configured but the operation failed."""


def enabled() -> bool:
    return bool(
        ROOT_FOLDER_ID and OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET and OAUTH_REFRESH_TOKEN
    )


def describe() -> dict:
    return {
        "enabled": enabled(),
        "root_folder_id": ROOT_FOLDER_ID or None,
        "auth": "oauth-refresh-token" if enabled() else None,
    }


def _get_service():
    """The calling thread's own Drive client, built on first use.

    Per-thread rather than per-call: building is cheap (static discovery, no
    network) but the first call on a new client refreshes the access token, and
    that token is good for an hour, so a thread that keeps its client avoids
    repeating it.
    """
    if not enabled():
        raise DriveError(
            "Google Drive is not configured. Set GOOGLE_DRIVE_ROOT_FOLDER_ID, "
            "GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET and "
            "GOOGLE_OAUTH_REFRESH_TOKEN."
        )

    service = getattr(_local, "service", None)
    if service is not None:
        return service

    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError as e:
        raise DriveError(
            "Drive support needs google-api-python-client and google-auth. "
            "Run: pip install -r requirements.txt"
        ) from e

    # Credentials are per-thread too. Sharing one would move the race from the
    # socket to the token refresh, which is no better.
    credentials = Credentials(
        token=None,
        refresh_token=OAUTH_REFRESH_TOKEN,
        token_uri=TOKEN_URI,
        client_id=OAUTH_CLIENT_ID,
        client_secret=OAUTH_CLIENT_SECRET,
        scopes=SCOPES,
    )
    _local.service = build("drive", "v3", credentials=credentials, cache_discovery=False)
    return _local.service


def _reset_service() -> None:
    """Drop this thread's client so the next call builds a fresh connection."""
    _local.service = None


def _with_reconnect(run, what: str):
    """Run some Drive work, reconnecting once if the socket turned out dead.

    Drive closes idle connections and httplib2 does not notice, so the next
    call on a long-lived client can fail on a socket that is already gone.
    Rebuilding and trying again makes that a hiccup rather than an error. Only
    transport failures are retried — an API rejection would fail identically
    the second time, so it is raised straight away.
    """
    for attempt in (1, 2):
        try:
            return run(_get_service())
        except TRANSPORT_ERRORS as e:
            _reset_service()
            if attempt == 2:
                raise DriveError(f"{what}: {e}") from e
        except Exception as e:
            raise DriveError(f"{what}: {e}") from e


def _execute(build_request, what: str):
    """One request, one response — the shape almost every call below has."""
    return _with_reconnect(lambda service: build_request(service).execute(), what)


def _escape(name: str) -> str:
    return name.replace("\\", "\\\\").replace("'", "\\'")


def get_or_create_folder(name: str, parent_id: Optional[str] = None) -> str:
    parent = parent_id or ROOT_FOLDER_ID
    cache_key = f"{parent}/{name}"

    with _lock:
        if cache_key in _folder_cache:
            return _folder_cache[cache_key]

        query = (
            f"name = '{_escape(name)}' and '{parent}' in parents "
            f"and mimeType = '{FOLDER_MIME}' and trashed = false"
        )
        what = f"Could not open or create Drive folder '{name}'"

        found = _execute(
            lambda s: s.files().list(q=query, fields="files(id)", pageSize=1), what
        ).get("files", [])

        if found:
            folder_id = found[0]["id"]
        else:
            folder_id = _execute(
                lambda s: s.files().create(
                    body={"name": name, "mimeType": FOLDER_MIME, "parents": [parent]},
                    fields="id",
                ),
                what,
            )["id"]

        _folder_cache[cache_key] = folder_id
        return folder_id


def find_file(name: str, parent_id: str) -> Optional[dict]:
    query = f"name = '{_escape(name)}' and '{parent_id}' in parents and trashed = false"

    files = _execute(
        lambda s: s.files().list(
            q=query, fields="files(id, name, webViewLink)", pageSize=1
        ),
        f"Could not search Drive for '{name}'",
    ).get("files", [])

    return files[0] if files else None


def list_names(parent_id: str) -> list[str]:
    files = _execute(
        lambda s: s.files().list(
            q=f"'{parent_id}' in parents and trashed = false",
            fields="files(name)",
            pageSize=200,
        ),
        "Could not list Drive folder",
    ).get("files", [])

    return [f["name"] for f in files]


def upload_file(local_path: Path, parent_id: str, name: Optional[str] = None) -> dict:
    """Upload (or replace by name) a local file. Returns id, name and link."""
    from googleapiclient.http import MediaFileUpload

    name = name or local_path.name
    mime, _ = mimetypes.guess_type(name)
    media = MediaFileUpload(str(local_path), mimetype=mime or "application/octet-stream",
                            resumable=False)
    what = f"Could not upload '{name}' to Drive"

    existing = find_file(name, parent_id)
    if existing:
        result = _execute(
            lambda s: s.files().update(
                fileId=existing["id"], media_body=media, fields="id, name, webViewLink"
            ),
            what,
        )
    else:
        result = _execute(
            lambda s: s.files().create(
                body={"name": name, "parents": [parent_id]},
                media_body=media,
                fields="id, name, webViewLink",
            ),
            what,
        )

    return {
        "drive_file_id": result.get("id"),
        "drive_name": result.get("name"),
        "drive_link": result.get("webViewLink"),
    }


def upload_json(data, name: str, parent_id: str) -> dict:
    """Write a JSON document to Drive, replacing any file of the same name."""
    from googleapiclient.http import MediaIoBaseUpload

    payload = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
    media = MediaIoBaseUpload(io.BytesIO(payload), mimetype="application/json",
                              resumable=False)
    what = f"Could not write '{name}' to Drive"

    existing = find_file(name, parent_id)
    if existing:
        result = _execute(
            lambda s: s.files().update(
                fileId=existing["id"], media_body=media, fields="id, name, webViewLink"
            ),
            what,
        )
    else:
        result = _execute(
            lambda s: s.files().create(
                body={"name": name, "parents": [parent_id]},
                media_body=media,
                fields="id, name, webViewLink",
            ),
            what,
        )

    return {
        "drive_file_id": result.get("id"),
        "drive_name": result.get("name"),
        "drive_link": result.get("webViewLink"),
    }


def download_bytes(file_id: str) -> tuple[bytes, str]:
    """Fetch a file's raw content and mime type.

    Drive share links are HTML viewer pages, not images, so anything that needs
    to render the file has to come through here.
    """
    what = "Could not download Drive file"

    meta = _execute(lambda s: s.files().get(fileId=file_id, fields="mimeType"), what)
    data = _with_reconnect(lambda s: _read_media(s, file_id), what)

    return data, meta.get("mimeType") or "application/octet-stream"


def _read_media(service, file_id: str) -> bytes:
    """Pull a file's bytes down in chunks.

    Takes a fresh buffer each call so a retry starts clean rather than
    appending to a half-finished download.
    """
    from googleapiclient.http import MediaIoBaseDownload

    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, service.files().get_media(fileId=file_id))

    done = False
    while not done:
        _, done = downloader.next_chunk()

    return buffer.getvalue()


def read_json(name: str, parent_id: str):
    """Read a JSON document back, or None if it isn't there."""
    existing = find_file(name, parent_id)

    if not existing:
        return None

    raw = _with_reconnect(
        lambda s: _read_media(s, existing["id"]),
        f"Could not read '{name}' from Drive",
    )

    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise DriveError(f"'{name}' in Drive is not valid JSON: {e}") from e


def rename_file(file_id: str, new_name: str) -> None:
    _execute(
        lambda s: s.files().update(fileId=file_id, body={"name": new_name}, fields="id"),
        "Could not rename Drive file",
    )


def move_file(file_id: str, new_parent_id: str, old_parent_id: str) -> None:
    _execute(
        lambda s: s.files().update(
            fileId=file_id, addParents=new_parent_id, removeParents=old_parent_id,
            fields="id",
        ),
        "Could not move Drive file",
    )
