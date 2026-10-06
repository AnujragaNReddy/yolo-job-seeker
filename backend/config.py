"""Configuration, all of it from the environment.

Defaults are chosen so a clean checkout with no credentials at all still does
something useful: three keyless job boards, matching, and letter drafting with
a template. Keys widen the search; they do not switch the app on.
"""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

DATA_DIR = Path(os.environ.get("DATA_DIR", PROJECT_ROOT / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

RESUME_DIR = DATA_DIR / "resume"
RESUME_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if origin.strip()
]

# How many postings to pull per source per scan. Higher is not better: the
# scan has to finish inside a request on a free instance, and the matcher is
# the point rather than the volume.
JOBS_PER_SOURCE = int(os.environ.get("JOBS_PER_SOURCE", "40"))

# Matches below this are discarded rather than stored. Keeping everything
# makes the list useless to read, which is the actual failure mode.
MIN_MATCH_SCORE = int(os.environ.get("MIN_MATCH_SCORE", "45"))

# Above this, the scan drafts a cover letter in advance so it is waiting
# rather than needing a click and a wait. This is the "automatic" part that
# is safe to automate.
AUTO_PREPARE_ABOVE = int(os.environ.get("AUTO_PREPARE_ABOVE", "75"))

# Letters drafted per scan. Each is an LLM call, and the free tier throttles,
# so this is a budget rather than a target.
MAX_PREPARE_PER_SCAN = int(os.environ.get("MAX_PREPARE_PER_SCAN", "5"))

# Postings older than this are dropped. A four-hourly scan otherwise keeps
# re-surfacing roles that closed weeks ago.
MAX_JOB_AGE_DAYS = int(os.environ.get("MAX_JOB_AGE_DAYS", "30"))

# Scan cadence for the in-process timer. Off by default: a free Render
# instance sleeps and kills its own threads, so the GitHub Action drives this
# instead. See .github/workflows/job-scan.yml.
SCAN_INTERVAL_SECONDS = int(os.environ.get("SCAN_INTERVAL_SECONDS", "0"))

PUBLIC_BASE_URL = os.environ.get(
    "PUBLIC_BASE_URL", "https://yolo-job-seeker-backend.onrender.com"
).strip().rstrip("/")


def describe() -> dict:
    return {
        "jobs_per_source": JOBS_PER_SOURCE,
        "min_match_score": MIN_MATCH_SCORE,
        "auto_prepare_above": AUTO_PREPARE_ABOVE,
        "max_prepare_per_scan": MAX_PREPARE_PER_SCAN,
        "max_job_age_days": MAX_JOB_AGE_DAYS,
        "scan_interval_seconds": SCAN_INTERVAL_SECONDS,
    }
