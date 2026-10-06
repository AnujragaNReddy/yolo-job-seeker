"""Durable storage for the profile, matches and applications.

Render's free disk is wiped on every restart, and free instances restart
constantly — so a resume uploaded on Monday would be gone by Tuesday. Every
document here is therefore written locally and mirrored to Drive, with Drive
treated as the real copy and the local file as a cache.

Three documents and one binary:

  profile.json       the parsed resume plus any manual corrections
  matches.json       scored postings, newest scan first
  applications.json  what has been drafted, opened and applied to
  <the resume file>  so a cover letter can be re-drafted after a restart

Drive being unavailable never fails a write. The data is still on disk and the
next write retries the mirror; losing durability is bad, but losing the
request that was in flight is worse.
"""

import json
import threading
from pathlib import Path
from typing import Any, Optional

import drive_store

DRIVE_FOLDER = "job-seeker"
DRIVE_RESUME_FOLDER = "resume"

PROFILE_FILE = "profile.json"
MATCHES_FILE = "matches.json"
APPLICATIONS_FILE = "applications.json"
PREFERENCES_FILE = "preferences.json"

# Read-modify-write is the normal shape here (appending an application,
# updating a status), and two requests arriving together would otherwise lose
# one of the writes.
_lock = threading.RLock()
_cache: dict[str, Any] = {}


def _folder_id() -> Optional[str]:
    if not drive_store.enabled():
        return None
    try:
        return drive_store.get_or_create_folder(DRIVE_FOLDER)
    except drive_store.DriveError:
        return None


def read(data_dir: Path, name: str, fallback: Any) -> Any:
    """Local first, Drive second, fallback last."""
    with _lock:
        if name in _cache:
            return _cache[name]

        path = data_dir / name
        data = None

        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                data = None

        if data is None:
            folder_id = _folder_id()
            if folder_id:
                try:
                    restored = drive_store.read_json(name, folder_id)
                    if restored is not None:
                        data = restored
                        # Put it back on disk so the next read is local.
                        try:
                            path.parent.mkdir(parents=True, exist_ok=True)
                            path.write_text(
                                json.dumps(data, indent=2, ensure_ascii=False),
                                encoding="utf-8",
                            )
                        except OSError:
                            pass
                except drive_store.DriveError:
                    data = None

        _cache[name] = fallback if data is None else data
        return _cache[name]


def write(data_dir: Path, name: str, data: Any) -> Any:
    with _lock:
        _cache[name] = data

        path = data_dir / name
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass

        folder_id = _folder_id()
        if folder_id:
            try:
                drive_store.upload_json(data, name, folder_id)
            except drive_store.DriveError:
                pass

        return data


def forget(name: Optional[str] = None) -> None:
    """Drop the in-memory copy so the next read goes back to disk or Drive."""
    with _lock:
        if name is None:
            _cache.clear()
        else:
            _cache.pop(name, None)


# --- the resume file itself -------------------------------------------------

def save_resume_file(local_path: Path) -> dict:
    """Mirror the uploaded resume to Drive.

    The file, not just the parsed text: re-drafting a letter or re-parsing
    with a better vocabulary needs the original, and after a restart the local
    copy is gone.
    """
    if not drive_store.enabled():
        return {"stored": False, "reason": "Drive is not configured."}

    try:
        folder_id = drive_store.get_or_create_folder(DRIVE_FOLDER)
        resume_folder = drive_store.get_or_create_folder(DRIVE_RESUME_FOLDER, folder_id)
        result = drive_store.upload_file(local_path, resume_folder)
    except drive_store.DriveError as e:
        return {"stored": False, "reason": str(e)}

    return {"stored": True, **result}


def fetch_resume_file(filename: str, destination: Path) -> bool:
    """Pull the resume back from Drive after a restart wiped the disk."""
    if not drive_store.enabled():
        return False

    try:
        folder_id = drive_store.get_or_create_folder(DRIVE_FOLDER)
        resume_folder = drive_store.get_or_create_folder(DRIVE_RESUME_FOLDER, folder_id)
        found = drive_store.find_file(filename, resume_folder)

        if not found:
            return False

        data, _ = drive_store.download_bytes(found["id"])
    except drive_store.DriveError:
        return False

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    except OSError:
        return False

    return True


def describe() -> dict:
    return {
        "drive": drive_store.describe(),
        "durable": drive_store.enabled(),
        "folder": DRIVE_FOLDER,
    }
