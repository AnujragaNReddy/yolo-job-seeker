"""Drafting a cover letter for one posting.

Two paths. The LLM one goes through Pollinations' OpenAI-compatible endpoint,
which is free and needs no key. The template one is pure string formatting and
always works, so a throttled or missing API degrades the quality rather than
breaking the feature.

The prompt is written to stop the model doing the thing that makes generated
cover letters dangerous rather than merely bad: inventing experience. A
fabricated employer or an invented certification is a lie told in the
candidate's name, to a recruiter, in writing. So the model is given the
matched skills and nothing else to work from, told the overlap explicitly, and
told not to claim anything outside it.

Every draft is returned as a draft. The UI labels it for review and never
sends anything anywhere — submitting is a human act here, by design.
"""

import json
import os
from typing import Optional

import requests

TEXT_ENDPOINT = "https://text.pollinations.ai/openai"

# The anonymous tier exposes one model. Named here so a failure is traceable
# rather than looking like a network problem.
MODEL = os.environ.get("LETTER_MODEL", "openai-fast").strip()

TIMEOUT_SECONDS = 90
MAX_DESCRIPTION_CHARS = 2500

SYSTEM_PROMPT = """You write short, specific cover letters for job applicants.

Absolute rules:
- Use ONLY the facts given to you. Never invent an employer, a job title, a
  date, a degree, a certification, a metric or a project.
- If the overlap between the candidate and the role is thin, write a shorter
  letter. Do not pad it with claims you cannot support.
- No flattery about the company, no "I have always been passionate about".
- Do not restate the job description back at them.
- Plain, direct sentences. No em dashes.

Structure: a one-line opening naming the role, two short paragraphs on the
relevant overlap, and a one-line close. Between 120 and 200 words. Sign off
with the candidate's name only."""


class LetterError(RuntimeError):
    """The letter could not be drafted."""


def _prompt(job: dict, profile: dict, matched: list[str]) -> str:
    name = profile.get("name") or "the candidate"
    years = profile.get("years_experience")
    titles = profile.get("titles") or []

    lines = [
        f"Role: {job.get('title', 'the role')}",
        f"Company: {job.get('company', 'the company')}",
        "",
        f"Candidate name: {name}",
    ]

    if titles:
        lines.append(f"Their background: {', '.join(titles[:3])}")
    if years is not None:
        lines.append(f"Years of experience: {years}")

    if matched:
        lines.append(
            "Skills the candidate has that this role asks for: "
            + ", ".join(matched[:10])
        )
    else:
        lines.append(
            "No specific skill overlap was detected. Keep the letter brief and "
            "general, and do not claim relevant experience."
        )

    description = (job.get("description") or "")[:MAX_DESCRIPTION_CHARS]
    if description:
        lines += ["", "Job description, for context only. Do not copy from it:",
                  description]

    return "\n".join(lines)


def _template(job: dict, profile: dict, matched: list[str]) -> str:
    """The fallback. Deliberately plain and obviously a starting point."""
    name = profile.get("name") or ""
    title = job.get("title") or "the role"
    company = job.get("company") or "your team"
    years = profile.get("years_experience")

    opening = f"I am writing to apply for the {title} position at {company}."

    if matched:
        overlap = ", ".join(matched[:6])
        middle = (
            f"My background lines up with what the role asks for, particularly "
            f"{overlap}. "
        )
        if years is not None:
            middle += (
                f"I have around {years} years of experience working with these, "
                "and would be glad to talk through specifics."
            )
        else:
            middle += "I would be glad to talk through specifics."
    else:
        middle = (
            "I am interested in the role and believe my experience is relevant. "
            "I would welcome the chance to explain why."
        )

    close = "Thank you for your time."

    return "\n\n".join([opening, middle, close] + ([name] if name else []))


def draft(job: dict, profile: dict, matched: Optional[list[str]] = None,
          use_llm: bool = True) -> dict:
    """Draft a letter. Always returns something usable."""
    matched = matched or []

    if not use_llm:
        return {"letter": _template(job, profile, matched), "generated_by": "template"}

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _prompt(job, profile, matched)},
        ],
        # Low, because the failure mode that matters is invention rather than
        # dullness.
        "temperature": 0.4,
        "max_tokens": 600,
    }

    try:
        response = requests.post(
            TEXT_ENDPOINT,
            json=payload,
            headers={"Content-Type": "application/json",
                     "Referer": "yolo-job-seeker"},
            timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        return {
            "letter": _template(job, profile, matched),
            "generated_by": "template",
            "note": "Could not reach the text model; used the template.",
        }

    if not response.ok:
        return {
            "letter": _template(job, profile, matched),
            "generated_by": "template",
            "note": f"Text model returned HTTP {response.status_code}; used the template.",
        }

    try:
        body = response.json()
        content = (
            ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        ).strip()
    except (ValueError, AttributeError, IndexError):
        content = ""

    # The free tier can spend its whole budget on reasoning and return nothing
    # in content, which is a success response with no letter in it.
    if len(content) < 80:
        return {
            "letter": _template(job, profile, matched),
            "generated_by": "template",
            "note": "Text model returned no usable text; used the template.",
        }

    return {"letter": content, "generated_by": MODEL}


def describe() -> dict:
    return {"endpoint": TEXT_ENDPOINT, "model": MODEL, "requires_key": False}
