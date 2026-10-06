"""Scoring a posting against a resume.

Every score comes with the reasons behind it. That is the whole design
constraint: a number on its own is unactionable, and worse, unfalsifiable. If
the UI claims 84%, it has to be able to say "12 of the 15 skills it asks for,
including Python, FastAPI and PostgreSQL" — because that is what tells someone
whether to trust it or fix their skill list.

Four components, weighted:

  skills      how much of what the posting asks for the resume has
  title       does the role resemble what this person does
  seniority   right level, or close to it
  location    remote, or somewhere plausible

Skills dominate on purpose. The others are corrections, and each is capable of
ruling a job out on its own, which is handled by the penalties rather than by
the weights.
"""

import re
from typing import Optional

import skills as skills_module

WEIGHTS = {
    "skills": 0.60,
    "title": 0.20,
    "seniority": 0.12,
    "location": 0.08,
}

SENIORITY_ORDER = ["intern", "junior", "mid", "senior", "lead", "principal", "director"]

SENIORITY_HINTS = {
    "intern": ["intern", "internship", "trainee", "apprentice"],
    "junior": ["junior", "graduate", "entry level", "entry-level", "fresher"],
    "mid": ["mid level", "mid-level"],
    "senior": ["senior", "sr.", "sr "],
    "lead": ["lead", "staff engineer", "team lead", "tech lead"],
    "principal": ["principal", "architect", "distinguished"],
    "director": ["director", "head of", "vp of", "vice president", "chief"],
}

REMOTE_WORDS = ["remote", "anywhere", "work from home", "wfh", "distributed",
                "telecommute"]

# A posting naming more years than someone has is not automatically out —
# listed requirements are routinely aspirational — but it is worth flagging and
# worth a dent in the score.
YEARS_REQUIRED = re.compile(
    r"(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?(?:years?|yrs?)", re.IGNORECASE
)


def _seniority_of(text: str) -> Optional[str]:
    lowered = f" {text.lower()} "

    # Highest first: "senior" inside "senior staff engineer" should not win
    # over the lead-level signal.
    for level in reversed(SENIORITY_ORDER):
        for hint in SENIORITY_HINTS.get(level, []):
            if hint in lowered:
                return level

    return None


def _years_required(text: str) -> Optional[int]:
    values = [int(v) for v in YEARS_REQUIRED.findall(text or "")]
    credible = [v for v in values if 0 < v <= 25]
    # The smallest stated requirement is the one to clear — a posting saying
    # "3+ years, ideally 8" is open to someone with four.
    return min(credible) if credible else None


def _title_score(job_title: str, profile_titles: list[str], resume_skills: list[str]
                 ) -> tuple[float, str]:
    title = (job_title or "").lower()

    if not title:
        return 0.5, "no title to compare"

    for candidate in profile_titles:
        if candidate.lower() in title or title in candidate.lower():
            return 1.0, f"title matches your {candidate} background"

    # No known title matched, so fall back to word overlap. A "Backend
    # Engineer" against a "Backend Developer" resume should not score zero
    # just because the noun differs.
    profile_words = {
        word
        for candidate in profile_titles
        for word in re.findall(r"[a-z]+", candidate.lower())
        if len(word) > 3
    }
    title_words = {w for w in re.findall(r"[a-z]+", title) if len(w) > 3}

    if profile_words and title_words:
        overlap = profile_words & title_words
        if overlap:
            share = len(overlap) / len(title_words)
            return min(1.0, 0.45 + share), f"title overlaps on {', '.join(sorted(overlap))}"

    # Last resort: a skill named in the title is a real signal ("Python
    # Developer" against a Python resume).
    for skill in resume_skills[:15]:
        if skill.lower() in title:
            return 0.7, f"title names {skill}"

    return 0.25, "title is unlike your previous roles"


def _seniority_score(job_title: str, job_description: str,
                     profile_seniority: str) -> tuple[float, str]:
    # The title first, and the description only as a fallback. A role's level
    # lives in its title; descriptions mention other levels constantly — "you
    # will report to the Head of Engineering" made a senior opening read as
    # director-level, three rungs up, and cost it most of its score.
    job_level = _seniority_of(job_title)

    if not job_level:
        # The opening lines only. Further down is boilerplate about the
        # leadership team, which says nothing about the vacancy.
        job_level = _seniority_of((job_description or "")[:400])

    if not job_level:
        return 0.7, "no stated level"

    try:
        gap = SENIORITY_ORDER.index(job_level) - SENIORITY_ORDER.index(profile_seniority)
    except ValueError:
        return 0.7, "level unclear"

    if gap == 0:
        return 1.0, f"{job_level} level matches yours"
    if gap == 1:
        return 0.75, f"{job_level} is a step up from {profile_seniority}"
    if gap == -1:
        return 0.6, f"{job_level} is a step below {profile_seniority}"
    if gap > 1:
        return 0.25, f"{job_level} is {gap} levels above {profile_seniority}"

    return 0.3, f"{job_level} is well below {profile_seniority}"


def _location_score(job, wants_remote: bool, locations: list[str]) -> tuple[float, str]:
    haystack = f"{job.location} {job.title}".lower()
    is_remote = job.remote or any(word in haystack for word in REMOTE_WORDS)

    if is_remote:
        return 1.0, "remote"

    if wants_remote and not locations:
        return 0.3, "on-site, and you asked for remote"

    for place in locations:
        if place.strip() and place.strip().lower() in haystack:
            return 1.0, f"in {place.strip()}"

    if not locations:
        return 0.6, "location not checked"

    return 0.25, f"{job.location or 'location'} is outside your list"


def score(job, profile: dict, preferences: Optional[dict] = None) -> dict:
    """Score one job. Returns the number and every reason for it."""
    preferences = preferences or {}

    resume_skills = profile.get("skills") or []
    top_skills = profile.get("top_skills") or resume_skills
    have = {s.lower() for s in resume_skills}

    text = f"{job.title}\n{job.description}"
    wanted = skills_module.extract(text)

    matched = [s for s in wanted if s.lower() in have]
    missing = [s for s in wanted if s.lower() not in have]

    reasons = []

    # --- skills
    if wanted:
        skills_component = len(matched) / len(wanted)
        reasons.append(
            f"{len(matched)} of {len(wanted)} skills it asks for"
            + (f": {', '.join(matched[:6])}" if matched else "")
        )
    else:
        # The posting named nothing recognisable. That is an absence of
        # evidence, not evidence of a fit, so it sits low — a thin posting for
        # a well-matching title can still surface on the title score.
        skills_component = 0.3
        reasons.append("posting lists no recognisable skills")

    # A posting that names one skill the resume happens to have is not a
    # better match than one naming twelve of which nine hit. Scale by how much
    # the posting actually told us.
    if wanted and len(wanted) < 3:
        skills_component *= 0.75
        reasons.append("thin skill list, treated with caution")

    title_component, title_reason = _title_score(job.title, profile.get("titles") or [],
                                                 top_skills)
    reasons.append(title_reason)

    seniority_component, seniority_reason = _seniority_score(
        job.title, job.description, profile.get("seniority") or "mid"
    )
    reasons.append(seniority_reason)

    location_component, location_reason = _location_score(
        job,
        bool(preferences.get("remote_only")),
        preferences.get("locations") or [],
    )
    reasons.append(location_reason)

    total = (
        WEIGHTS["skills"] * skills_component
        + WEIGHTS["title"] * title_component
        + WEIGHTS["seniority"] * seniority_component
        + WEIGHTS["location"] * location_component
    )

    # --- penalties, for things that should rule a job out rather than nudge it
    penalties = []

    required_years = _years_required(text)
    have_years = profile.get("years_experience")

    if required_years and have_years is not None and required_years > have_years + 2:
        total *= 0.7
        penalties.append(f"asks for {required_years}+ years, you have about {have_years}")

    if preferences.get("remote_only") and location_component < 0.5:
        total *= 0.6
        penalties.append("not remote")

    if matched and len(matched) == 1 and len(wanted) > 6:
        total *= 0.8
        penalties.append("only one skill in common")

    # Nothing in common on either axis that matters. Without this, an
    # unrelated posting coasted into the top five on the location score alone:
    # a remote sales contract scored 41% against a backend resume, because
    # "remote" is worth 8% and an unreadable skill list still paid out. Being
    # remote is not a qualification.
    if not matched and title_component < 0.5:
        total *= 0.45
        penalties.append("no skills or title overlap with your profile")
    elif not matched and wanted:
        # The posting was specific about what it wants and the resume has none
        # of it, which is a clearer no than an empty description.
        total *= 0.55
        penalties.append(f"none of its {len(wanted)} listed skills")

    return {
        "score": round(max(0.0, min(1.0, total)) * 100),
        "matched_skills": matched,
        "missing_skills": missing[:12],
        "reasons": [r for r in reasons if r],
        "penalties": penalties,
        "components": {
            "skills": round(skills_component * 100),
            "title": round(title_component * 100),
            "seniority": round(seniority_component * 100),
            "location": round(location_component * 100),
        },
        "required_years": required_years,
    }


def rank(jobs: list, profile: dict, preferences: Optional[dict] = None,
         minimum: int = 0) -> list[dict]:
    """Score every job, keep what clears the bar, best first."""
    scored = []

    for job in jobs:
        result = score(job, profile, preferences)
        if result["score"] < minimum:
            continue
        scored.append({**job.to_dict(), "match": result})

    scored.sort(key=lambda row: row["match"]["score"], reverse=True)
    return scored


def build_query(profile: dict, preferences: Optional[dict] = None) -> str:
    """What to actually send to the boards.

    Most of these APIs take one free-text string, so this picks the single
    most representative term rather than concatenating everything — a query of
    twenty skills matches nothing on a keyword search.
    """
    preferences = preferences or {}

    explicit = (preferences.get("query") or "").strip()
    if explicit:
        return explicit

    titles = profile.get("titles") or []
    if titles:
        return titles[0]

    top = profile.get("top_skills") or profile.get("skills") or []
    if top:
        return top[0]

    return "developer"
