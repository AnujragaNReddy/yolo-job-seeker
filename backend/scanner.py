"""One scan: search every board, score, store, draft ahead.

This is what the four-hourly job runs. The sequence matters more than any
individual step:

  1. query every enabled source, tolerating individual failures
  2. drop postings that are too old, or already settled
  3. de-duplicate, because aggregators carry the same opening
  4. score against the profile and discard what does not clear the bar
  5. draft letters for the best few so they are waiting, not pending

Step 2 is what makes repeated scanning bearable. Without it, every four hours
would resurface roles already applied to, and the list would become something
to scroll past rather than work through.

A source failing is normal — free APIs rate-limit, go down, change shape — so
one failure never fails the scan. The result says which sources answered and
which did not, because "fewer results than usual" with no explanation is the
kind of silence that wastes an afternoon.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import applications
import config
import cover_letter
import matcher
import sources as sources_module
import store


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_date(value: str) -> Optional[datetime]:
    """Whatever the board sent, as a UTC datetime.

    Returns None for anything unreadable, and callers treat None as "keep" —
    dropping a posting because its date was in a format nobody anticipated
    would be a silent, unexplainable gap in the results.
    """
    if not value:
        return None

    text = str(value).strip()

    # Arbeitnow sends Unix seconds. Parsed before the ISO attempts, because
    # fromisoformat happens to accept some bare digit strings as a year and
    # would turn 1791309339 into something nonsensical rather than failing.
    if text.isdigit():
        try:
            seconds = int(text)
        except ValueError:
            return None
        # Milliseconds if it is far too large to be seconds.
        if seconds > 10_000_000_000:
            seconds //= 1000
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    text = text.replace("Z", "+00:00")

    # Boards use half a dozen formats between them.
    for attempt in (text, text[:19], text[:10]):
        try:
            parsed = datetime.fromisoformat(attempt)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    return None


def _too_old(job, max_age_days: int) -> bool:
    posted = _parse_date(job.posted_at)
    if posted is None:
        return False
    return posted < datetime.now(timezone.utc) - timedelta(days=max_age_days)


def collect(query: str, limit_per_source: int) -> tuple[list, list[dict]]:
    """Every posting the enabled sources return, plus a per-source report."""
    found: list = []
    report: list[dict] = []

    for source in sources_module.build_sources():
        if not source.enabled():
            report.append({
                "source": source.name, "ok": None,
                "reason": "not configured", "count": 0,
            })
            continue

        try:
            jobs = source.fetch(query, limit=limit_per_source)
            found.extend(jobs)
            report.append({"source": source.name, "ok": True, "count": len(jobs)})
        except Exception as e:
            # Deliberately broad: a board returning unexpected JSON raises
            # KeyError or TypeError rather than SourceError, and no board
            # should be able to fail the whole scan.
            report.append({
                "source": source.name, "ok": False,
                "reason": str(e)[:200], "count": 0,
            })

    return found, report


def dedupe(jobs: list) -> list:
    """One entry per opening, preferring the one with the most detail.

    Aggregators list the same role, and the copy with the fuller description
    scores better and drafts a better letter, so richness wins over arrival
    order.
    """
    best: dict = {}

    for job in jobs:
        existing = best.get(job.id)
        if existing is None or len(job.description) > len(existing.description):
            best[job.id] = job

    return list(best.values())


def run(data_dir: Path, profile: dict, preferences: Optional[dict] = None,
        prepare: bool = True) -> dict:
    """Do a full scan and persist the results."""
    preferences = preferences or {}

    if not profile or not profile.get("skills"):
        return {
            "scanned": False,
            "reason": "No resume profile yet. Upload a resume first.",
        }

    query = matcher.build_query(profile, preferences)
    limit = int(preferences.get("jobs_per_source") or config.JOBS_PER_SOURCE)
    minimum = int(preferences.get("min_match_score") or config.MIN_MATCH_SCORE)
    max_age = int(preferences.get("max_job_age_days") or config.MAX_JOB_AGE_DAYS)

    raw, report = collect(query, limit)

    fresh = [job for job in raw if not _too_old(job, max_age)]
    unique = dedupe(fresh)

    # Already applied to, skipped or closed: scored but not offered again.
    settled = applications.settled_ids(data_dir)
    offerable = [job for job in unique if job.id not in settled]

    ranked = matcher.rank(offerable, profile, preferences, minimum=minimum)

    # Track everything that cleared the bar, so a re-scan knows it has been
    # seen and the tracker is the single source of truth.
    for row in ranked:
        applications.upsert(data_dir, row, state="matched")

    prepared = []

    if prepare:
        threshold = int(preferences.get("auto_prepare_above")
                        or config.AUTO_PREPARE_ABOVE)
        budget = int(preferences.get("max_prepare_per_scan")
                     or config.MAX_PREPARE_PER_SCAN)

        for row in ranked:
            if len(prepared) >= budget:
                break
            if row["match"]["score"] < threshold:
                # ranked is sorted, so nothing below here qualifies either.
                break

            record = applications.get(data_dir, row["id"]) or {}
            if record.get("cover_letter"):
                continue

            result = cover_letter.draft(
                row, profile, row["match"]["matched_skills"]
            )
            applications.upsert(
                data_dir, row, state="prepared",
                cover_letter=result["letter"],
                letter_generated_by=result.get("generated_by"),
                letter_note=result.get("note"),
            )
            prepared.append(row["id"])

    payload = {
        "version": 1,
        "scanned_at": _now(),
        "query": query,
        "sources": report,
        "counts": {
            "returned": len(raw),
            "fresh": len(fresh),
            "unique": len(unique),
            "offerable": len(offerable),
            "matched": len(ranked),
            "prepared": len(prepared),
        },
        "min_match_score": minimum,
        "items": ranked,
    }

    store.write(data_dir, store.MATCHES_FILE, payload)

    return {
        "scanned": True,
        "scanned_at": payload["scanned_at"],
        "query": query,
        "sources": report,
        "counts": payload["counts"],
        "prepared": prepared,
    }


def latest(data_dir: Path) -> dict:
    return store.read(
        data_dir, store.MATCHES_FILE,
        {"version": 1, "scanned_at": None, "items": [], "counts": {}, "sources": []},
    )
