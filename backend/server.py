"""HTTP API for the job seeker.

Deliberately absent: anything that submits an application. No major board
offers an API for it, all of them forbid automated submission, and sending
unreviewed material to a recruiter in someone's name is not a thing to
automate quietly. So the API drafts, tracks and opens; a person submits.

/api/applications/{id}/state is where that line sits. The app sets 'opened'
because it opened the link. Only an explicit call sets 'applied'.
"""

import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import applications
import config
import cover_letter
import drive_store
import matcher
import resume_parser
import scanner
import skills
import sources as sources_module
import store
import yolo_auth

app = FastAPI(title="yolo-job-seeker", version="1.0.0")

# Sign-in, applied to everything except the paths named here.
#
# /api/health is public because the scheduled job pings it to wake a sleeping
# instance before it can send anything else, and because a health check that
# needs credentials is not a health check.
#
# /api/scan takes the service token because the four-hourly GitHub Action
# drives it and has no browser to sign in with.
#
# Everything else - the resume, the profile, the matches, the tracker - is
# private by default. That is the right default here more than in the sibling
# projects: this one holds a CV, an employment history and a record of where
# someone has applied for work.
yolo_auth.install_auth(
    app,
    public=("/api/health", "/docs", "/openapi.json", "/redoc"),
    service=("/api/scan",),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)



# A scan takes minutes across several boards, which is far longer than a
# request should block. One worker, because the free instance has one core and
# concurrent scans would only fight each other.
_executor = ThreadPoolExecutor(max_workers=1)

# Uploads are read into memory before being written, so this is the real cap
# on that. A resume above it is not a resume.
MAX_RESUME_BYTES = 8 * 1024 * 1024

DEFAULT_PREFERENCES = {
    "query": "",
    "locations": [],
    "remote_only": False,
    "min_match_score": config.MIN_MATCH_SCORE,
    "auto_prepare_above": config.AUTO_PREPARE_ABOVE,
    "max_prepare_per_scan": config.MAX_PREPARE_PER_SCAN,
    "jobs_per_source": config.JOBS_PER_SOURCE,
    "max_job_age_days": config.MAX_JOB_AGE_DAYS,
    "use_llm_letters": True,
}


def preferences() -> dict:
    saved = store.read(config.DATA_DIR, store.PREFERENCES_FILE, {})
    return {**DEFAULT_PREFERENCES, **(saved if isinstance(saved, dict) else {})}


def profile() -> dict:
    return store.read(config.DATA_DIR, store.PROFILE_FILE, {})


# --- basics -----------------------------------------------------------------

@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "has_resume": bool(profile().get("skills")),
        "storage": store.describe(),
        "config": config.describe(),
    }


@app.get("/api/sources")
def list_sources():
    """Every board, enabled or not, so the UI can show what a key unlocks."""
    described = sources_module.describe_sources()
    return {
        "sources": described,
        "enabled": sum(1 for s in described if s["enabled"]),
        "total": len(described),
    }


@app.get("/api/drive")
def drive_status():
    return {**drive_store.describe(), "durable": drive_store.enabled()}


# --- resume and profile -----------------------------------------------------

@app.post("/api/resume")
async def upload_resume(file: UploadFile = File(...)):
    """Accept a resume, parse it, and keep both the file and the profile."""
    suffix = Path(file.filename or "").suffix.lower()

    if suffix not in resume_parser.SUPPORTED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{suffix or 'That file type'} is not supported. Upload one of: "
                f"{', '.join(sorted(resume_parser.SUPPORTED_SUFFIXES))}."
            ),
        )

    payload = await file.read()

    if not payload:
        raise HTTPException(status_code=400, detail="That file was empty.")

    if len(payload) > MAX_RESUME_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Resumes are capped at {MAX_RESUME_BYTES // (1024 * 1024)}MB.",
        )

    # Keep the extension, drop the rest of the name: it reaches the filesystem
    # and Drive, and a supplied filename is not something to trust with a path.
    safe_name = f"resume{suffix}"
    target = config.RESUME_DIR / safe_name

    try:
        config.RESUME_DIR.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Could not save the file: {e}")

    try:
        text = resume_parser.extract_text(target)
    except resume_parser.ResumeError as e:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=str(e))

    parsed = resume_parser.build_profile(text, filename=safe_name)
    # The text is kept so a letter can be re-drafted, and so changing the
    # skill vocabulary does not require re-uploading.
    parsed["resume_text"] = text
    parsed["drive"] = store.save_resume_file(target)

    store.write(config.DATA_DIR, store.PROFILE_FILE, parsed)

    return {"uploaded": True, "profile": _public_profile(parsed)}


def _public_profile(record: dict) -> dict:
    """The profile without the full resume text.

    The text is several pages of someone's employment history; it is needed
    server-side for drafting and nowhere in the UI, so it does not go over
    the wire on every poll.
    """
    return {k: v for k, v in (record or {}).items() if k != "resume_text"}


@app.get("/api/profile")
def get_profile():
    record = profile()
    return {
        "profile": _public_profile(record),
        "has_resume": bool(record.get("skills")),
        "query": matcher.build_query(record, preferences()) if record else "",
    }


class ProfileUpdate(BaseModel):
    """Corrections. Parsing a resume is guesswork, and a wrong skill list
    quietly poisons every match, so it has to be fixable."""

    name: Optional[str] = Field(default=None, max_length=120)
    skills: Optional[list[str]] = None
    titles: Optional[list[str]] = None
    seniority: Optional[str] = None
    years_experience: Optional[int] = Field(default=None, ge=0, le=60)


@app.put("/api/profile")
def update_profile(update: ProfileUpdate):
    record = profile()

    if not record:
        raise HTTPException(status_code=404, detail="Upload a resume first.")

    changes = update.model_dump(exclude_none=True)

    if "skills" in changes:
        # Filtered against the taxonomy: a skill nothing can match is worse
        # than no skill, because it looks like it is working.
        changes["skills"] = skills.known(changes["skills"])

    if "seniority" in changes:
        level = str(changes["seniority"]).strip().lower()
        if level not in resume_parser.SENIORITY_ORDER:
            raise HTTPException(
                status_code=400,
                detail=f"Seniority must be one of: {', '.join(resume_parser.SENIORITY_ORDER)}.",
            )
        changes["seniority"] = level

    updated = {**record, **changes, "edited": True}
    store.write(config.DATA_DIR, store.PROFILE_FILE, updated)

    return {"profile": _public_profile(updated)}


@app.delete("/api/resume")
def delete_resume():
    """Remove the resume and everything derived from it."""
    for name in (store.PROFILE_FILE, store.MATCHES_FILE):
        store.write(config.DATA_DIR, name, {} if name == store.PROFILE_FILE
                    else {"version": 1, "items": []})

    try:
        shutil.rmtree(config.RESUME_DIR, ignore_errors=True)
        config.RESUME_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    # Applications are deliberately left alone: a record of where someone
    # applied outlives the resume that produced it.
    return {"deleted": True, "applications_kept": applications.counts(config.DATA_DIR)}


# --- settings ---------------------------------------------------------------

class PreferencesUpdate(BaseModel):
    query: Optional[str] = Field(default=None, max_length=200)
    locations: Optional[list[str]] = None
    remote_only: Optional[bool] = None
    min_match_score: Optional[int] = Field(default=None, ge=0, le=100)
    auto_prepare_above: Optional[int] = Field(default=None, ge=0, le=100)
    max_prepare_per_scan: Optional[int] = Field(default=None, ge=0, le=25)
    jobs_per_source: Optional[int] = Field(default=None, ge=1, le=100)
    max_job_age_days: Optional[int] = Field(default=None, ge=1, le=365)
    use_llm_letters: Optional[bool] = None


@app.get("/api/settings")
def get_settings():
    return {"settings": preferences(), "defaults": DEFAULT_PREFERENCES}


@app.put("/api/settings")
def update_settings(update: PreferencesUpdate):
    changes = update.model_dump(exclude_none=True)

    if "locations" in changes:
        changes["locations"] = [
            str(place).strip()[:80] for place in changes["locations"]
            if str(place).strip()
        ][:10]

    merged = {**preferences(), **changes}
    store.write(config.DATA_DIR, store.PREFERENCES_FILE, merged)

    return {"settings": merged}


# --- scanning ---------------------------------------------------------------

@app.post("/api/scan")
def scan(prepare: bool = True):
    """Search every board and score the results.

    Runs inline rather than in the background: the caller is either a person
    who wants to see the outcome or the scheduled job, which needs the exit
    status to report a failure.
    """
    record = profile()

    if not record.get("skills"):
        raise HTTPException(
            status_code=409,
            detail="No resume yet. Upload one before scanning.",
        )

    future = _executor.submit(
        scanner.run, config.DATA_DIR, record, preferences(), prepare
    )

    try:
        return future.result()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Scan failed: {e}")


@app.get("/api/matches")
def matches(limit: int = 40, offset: int = 0, state: Optional[str] = None,
            minimum: int = 0):
    """The last scan's results, with each job's tracking state attached."""
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    data = scanner.latest(config.DATA_DIR)
    tracked = applications.load(config.DATA_DIR).get("items", {})

    rows = []

    for item in data.get("items", []):
        record = tracked.get(item.get("id")) or {}
        item_state = record.get("state", "matched")

        if state and item_state != state:
            continue
        if item.get("match", {}).get("score", 0) < minimum:
            continue

        rows.append({
            **item,
            "state": item_state,
            "has_letter": bool(record.get("cover_letter")),
        })

    window = rows[offset:offset + limit]

    return {
        "items": window,
        "count": len(window),
        "total": len(rows),
        "offset": offset,
        "next_offset": offset + limit if offset + limit < len(rows) else None,
        "scanned_at": data.get("scanned_at"),
        "query": data.get("query"),
        "sources": data.get("sources", []),
        "scan_counts": data.get("counts", {}),
    }


# --- applications -----------------------------------------------------------

@app.get("/api/applications")
def list_applications(state: Optional[str] = None):
    return {
        "applications": applications.listing(config.DATA_DIR, state),
        "counts": applications.counts(config.DATA_DIR),
        "states": applications.STATES,
    }


@app.get("/api/applications/{job_id}")
def get_application(job_id: str):
    record = applications.get(config.DATA_DIR, job_id)

    if not record:
        raise HTTPException(status_code=404, detail="Nothing tracked for that job.")

    return record


def _job_from_matches(job_id: str) -> Optional[dict]:
    for item in scanner.latest(config.DATA_DIR).get("items", []):
        if item.get("id") == job_id:
            return item
    return None


@app.post("/api/applications/{job_id}/prepare")
def prepare_application(job_id: str, regenerate: bool = False):
    """Draft a cover letter for one job.

    The draft is a draft. It is never sent anywhere, and the UI labels it for
    review, because the model is working from a parsed resume and a scraped
    description and either can be wrong.
    """
    record = profile()

    if not record.get("skills"):
        raise HTTPException(status_code=409, detail="Upload a resume first.")

    existing = applications.get(config.DATA_DIR, job_id)

    if existing and existing.get("cover_letter") and not regenerate:
        return {"prepared": True, "cached": True, "application": existing}

    job = _job_from_matches(job_id)

    if not job:
        raise HTTPException(
            status_code=404,
            detail="That job is not in the latest scan. Run a scan and try again.",
        )

    result = cover_letter.draft(
        job,
        record,
        (job.get("match") or {}).get("matched_skills", []),
        use_llm=bool(preferences().get("use_llm_letters", True)),
    )

    application = applications.upsert(
        config.DATA_DIR, job, state="prepared",
        cover_letter=result["letter"],
        letter_generated_by=result.get("generated_by"),
        letter_note=result.get("note"),
    )

    return {"prepared": True, "cached": False, "application": application}


class StateUpdate(BaseModel):
    state: str
    note: Optional[str] = Field(default=None, max_length=1000)


@app.post("/api/applications/{job_id}/state")
def update_application_state(job_id: str, update: StateUpdate):
    """Move an application along.

    'applied' only ever arrives here, from a person. Nothing infers it: the
    one record that has to be trustworthy is the record of where someone
    actually applied.
    """
    if update.state not in applications.STATES:
        raise HTTPException(
            status_code=400,
            detail=f"State must be one of: {', '.join(applications.STATES)}.",
        )

    fields = {"note": update.note} if update.note else {}

    try:
        record = applications.set_state(
            config.DATA_DIR, job_id, update.state, **fields
        )
    except KeyError:
        # Not tracked yet, which happens when acting on a job straight out of
        # a fresh scan. Create it in the requested state rather than erroring.
        job = _job_from_matches(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="No such job.")
        record = applications.upsert(config.DATA_DIR, job, state=update.state, **fields)

    return {"application": record}


@app.get("/api/letter-model")
def letter_model():
    return cover_letter.describe()


# --- optional in-process timer ----------------------------------------------

def _scan_loop(interval: int) -> None:
    """Scan on a timer, for a host that stays awake.

    Off by default, and it should stay off on Render: a free instance sleeps
    after ~15 minutes idle and takes its threads with it, so this would fire
    unpredictably or not at all. The GitHub Action in
    .github/workflows/job-scan.yml is the reliable scheduler there — it also
    wakes the service, which is half of why it works.
    """
    import time

    while True:
        time.sleep(interval)

        record = profile()
        if not record.get("skills"):
            continue

        try:
            scanner.run(config.DATA_DIR, record, preferences(), prepare=True)
        except Exception as e:
            # A failed scan must not kill the loop; the next one may well work.
            print(f"[scan-loop] scan failed: {e}", flush=True)


@app.on_event("startup")
def start_scan_loop():
    if config.SCAN_INTERVAL_SECONDS <= 0:
        return

    import threading

    threading.Thread(
        target=_scan_loop,
        args=(config.SCAN_INTERVAL_SECONDS,),
        daemon=True,
        name="scan-loop",
    ).start()
    print(
        f"[scan-loop] scanning every {config.SCAN_INTERVAL_SECONDS}s",
        flush=True,
    )
