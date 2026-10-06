"""Tracking what has been drafted, opened and applied to.

The states exist because "did I already apply to this?" is the question this
app has to answer well. A four-hourly scan re-finds the same postings, and
without a record it would keep presenting roles already applied to a week ago,
which is worse than useless once there are a few hundred of them.

  matched   found and scored, nothing done yet
  prepared  a cover letter has been drafted and is waiting
  opened    the real posting was opened in a browser
  applied   confirmed as submitted, by the person
  skipped   deliberately passed over
  closed    the posting went away

Two of those deserve comment. 'opened' is recorded by the app, because it
opens the link. 'applied' is only ever set by the person, because only they
know whether they finished the form — inferring it from a click would quietly
corrupt the one record that has to be trustworthy.
"""

import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import store

STATES = ["matched", "prepared", "opened", "applied", "skipped", "closed"]

# States that mean "do not offer this again".
SETTLED = {"applied", "skipped", "closed"}

_lock = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load(data_dir: Path) -> dict:
    """job id -> application record."""
    data = store.read(data_dir, store.APPLICATIONS_FILE, {"version": 1, "items": {}})

    # Tolerate an older or hand-edited file rather than crashing on it.
    if not isinstance(data, dict) or not isinstance(data.get("items"), dict):
        return {"version": 1, "items": {}}

    return data


def save(data_dir: Path, data: dict) -> dict:
    return store.write(data_dir, store.APPLICATIONS_FILE, data)


def get(data_dir: Path, job_id: str) -> Optional[dict]:
    return load(data_dir).get("items", {}).get(job_id)


def upsert(data_dir: Path, job: dict, state: str = "matched",
           **fields) -> dict:
    """Create or update the record for a job, keeping its history.

    Never downgrades. A scan re-finding a job already marked applied must not
    reset it to matched, which is exactly what a naive write would do on every
    cycle.
    """
    if state not in STATES:
        raise ValueError(f"Unknown state '{state}'. One of: {', '.join(STATES)}")

    with _lock:
        data = load(data_dir)
        items = data.setdefault("items", {})
        job_id = job.get("id") or job.get("job_id")

        if not job_id:
            raise ValueError("A job needs an id before it can be tracked.")

        existing = items.get(job_id)

        if existing:
            current_rank = STATES.index(existing.get("state", "matched"))
            new_rank = STATES.index(state)
            # Settled states are never walked back by an automated scan.
            keep_state = (
                existing["state"]
                if existing.get("state") in SETTLED and state == "matched"
                else (state if new_rank >= current_rank else existing["state"])
            )

            record = {**existing, **fields, "state": keep_state,
                      "updated_at": _now()}

            if keep_state != existing.get("state"):
                record.setdefault("history", []).append(
                    {"state": keep_state, "at": _now()}
                )
        else:
            record = {
                "job_id": job_id,
                "title": job.get("title", ""),
                "company": job.get("company", ""),
                "url": job.get("url", ""),
                "source": job.get("source", ""),
                "location": job.get("location", ""),
                "score": (job.get("match") or {}).get("score"),
                "matched_skills": (job.get("match") or {}).get("matched_skills", []),
                "state": state,
                "created_at": _now(),
                "updated_at": _now(),
                "history": [{"state": state, "at": _now()}],
                **fields,
            }

        items[job_id] = record
        save(data_dir, data)
        return record


def set_state(data_dir: Path, job_id: str, state: str, **fields) -> dict:
    """Move a record to a state explicitly, including backwards.

    Unlike upsert this does allow a downgrade, because a person correcting a
    mistake is the one case where going back is right.
    """
    if state not in STATES:
        raise ValueError(f"Unknown state '{state}'. One of: {', '.join(STATES)}")

    with _lock:
        data = load(data_dir)
        items = data.setdefault("items", {})
        record = items.get(job_id)

        if not record:
            raise KeyError(f"Nothing tracked for job {job_id}.")

        if record.get("state") != state:
            record.setdefault("history", []).append({"state": state, "at": _now()})

        record.update(fields)
        record["state"] = state
        record["updated_at"] = _now()

        items[job_id] = record
        save(data_dir, data)
        return record


def listing(data_dir: Path, state: Optional[str] = None) -> list[dict]:
    items = list(load(data_dir).get("items", {}).values())

    if state:
        items = [item for item in items if item.get("state") == state]

    items.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    return items


def settled_ids(data_dir: Path) -> set:
    """Jobs that should not be offered again."""
    return {
        job_id
        for job_id, record in load(data_dir).get("items", {}).items()
        if record.get("state") in SETTLED
    }


def counts(data_dir: Path) -> dict:
    tally = {state: 0 for state in STATES}

    for record in load(data_dir).get("items", {}).values():
        state = record.get("state")
        if state in tally:
            tally[state] += 1

    return tally
