# yolo-job-seeker

Upload a resume, get back the jobs on the market that actually match it,
ranked and explained, with a cover letter already drafted for the best ones.
Rescans every four hours.

## What it does not do, and why

**It does not submit applications.** That was the original ask, and it is not
buildable as described:

- No major job board has a public API for submitting an application. Not
  LinkedIn, not Indeed, not Glassdoor. Greenhouse and Lever do have apply
  APIs, but they authenticate the *employer*, not the candidate.
- All of them forbid automated submission in their terms, and enforce it.
  LinkedIn bans accounts for it and has litigated over automated access.
- Mass-submitting unreviewed applications is also the thing that gets a real
  candidate filtered out: recruiters and ATS systems notice, and a generated
  letter with a claim you cannot back up is worse than no letter.

So everything up to the submit button is automated: searching, parsing,
matching, scoring, explaining, drafting, and tracking. You open the posting
and send it. The tracker remembers which ones you did, and a later scan will
not offer them again.

## How it works

```
resume (pdf/docx/txt)
  -> text extraction            resume_parser.py
  -> skills, titles, seniority  skills.py  (curated vocabulary, not a model)
  -> search query               matcher.build_query

every 4 hours, or on demand
  -> query every enabled board  sources/
  -> drop stale and settled     scanner.py
  -> de-duplicate              (same role, several aggregators)
  -> score against the profile  matcher.py
  -> draft letters for the best cover_letter.py
  -> store                      store.py -> Google Drive
```

### Scoring

Four weighted components, and every score ships with its reasons:

| Component | Weight | What it measures |
|---|---|---|
| skills | 60% | how much of what the posting asks for you have |
| title | 20% | does the role resemble what you do |
| seniority | 12% | right level, or close |
| location | 8% | remote, or somewhere you listed |

Plus penalties that can rule a job out rather than nudge it: asking for far
more years than you have, not being remote when you require remote, and
having no skill or title overlap at all. That last one exists because without
it an unrelated remote sales contract scored 41% against a backend resume —
being remote is not a qualification.

The reasons are the point. A bare number is unactionable and, worse,
unfalsifiable; "12 of the 15 skills it asks for, including Python and
PostgreSQL" tells you whether to trust the ranking or go fix your skill list.

### Why a vocabulary and not an NLP model

`skills.py` is a curated alias table matched on word boundaries. Two reasons:
a 512MB free instance cannot hold spaCy or a transformer alongside everything
else, and the result has to be explainable. The hard part of this problem is
the vocabulary, not the parsing.

The boundary rules are where the bugs live, so they are deliberate: `r`, `c`
and `go` never match as bare letters; `react` must not match inside
`reactive`; `c++` and `c#` end in punctuation that `\b` cannot handle; and
`ci/cd`, `ci-cd`, `CI CD` and `cicd` are one skill written four ways.

## Job boards

Three need no credentials and are always on. Everything else is optional and
only widens the search.

| Board | Key needed | Notes |
|---|---|---|
| Remotive | no | remote roles, good descriptions |
| Arbeitnow | no | Europe-weighted |
| The Muse | no | a key only raises the rate limit |
| Greenhouse | no, but needs company slugs | public per-company boards |
| Lever | no, but needs company slugs | public per-company boards |
| Adzuna | `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` | broad, has salary data; free at developer.adzuna.com |
| Jooble | `JOOBLE_API_KEY` | free at jooble.org/api/about |
| USAJOBS | `USAJOBS_EMAIL` + `USAJOBS_API_KEY` | US federal only; free at developer.usajobs.gov |

For Greenhouse and Lever, set the company slugs from their board URLs:

```
GREENHOUSE_COMPANIES=stripe,figma
LEVER_COMPANIES=netflix,plaid
```

These are the JSON endpoints companies use to embed their own careers pages —
documented and intended for consumption, not scraped HTML.

## Running it locally

Backend:

```bash
cd backend
python -m venv .venv && . .venv/Scripts/activate   # or bin/activate
pip install -r requirements.txt
uvicorn server:app --reload
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. The dev server proxies `/api/*` to port 8000, so
no base URL or CORS setup is needed while developing.

## Durable storage with Google Drive

Render's free disk is wiped on every restart, and free instances restart
constantly — so without this, a resume uploaded on Monday is gone by Tuesday,
along with every application record.

Service accounts do not work for this: they have no Drive storage quota of
their own and cannot create files even inside a folder shared with them. It
has to be OAuth delegation against your own account.

1. Google Cloud console -> new project -> enable the **Google Drive API**.
2. OAuth consent screen -> External -> add yourself as a test user.
3. Credentials -> OAuth client ID -> **Desktop app**. Note the id and secret.
4. Make a Drive folder for this and take its id from the URL:
   `drive.google.com/drive/folders/<THIS>`.
5. Mint a refresh token locally, then set these four on Render:

```
GOOGLE_DRIVE_ROOT_FOLDER_ID
GOOGLE_OAUTH_CLIENT_ID
GOOGLE_OAUTH_CLIENT_SECRET
GOOGLE_OAUTH_REFRESH_TOKEN
```

`/api/health` reports `storage.durable`, and the UI shows a **Drive off** chip
when it is false — a refresh that is saved nowhere durable quietly defeats the
point, so it says so rather than letting you assume.

Note that the Drive client is **not** thread-safe: a `Resource` wraps one
connection, and two threads sharing it interleave on the same socket and
surface as `[SSL: WRONG_VERSION_NUMBER]`, which reads like a TLS
misconfiguration and is not one. `drive_store.py` gives each thread its own
client for exactly that reason.

## The four-hourly scan

`.github/workflows/job-scan.yml` posts to `/api/scan` every four hours. It
runs there rather than in-process because a free Render instance sleeps after
~15 minutes idle and takes its background threads with it — the Action also
wakes the service, which is half of why it works.

`SCAN_INTERVAL_SECONDS` enables an in-process timer instead, for a host that
stays awake. It is 0 by default.

The run summary reports per-source failures and a match count of zero,
because "fewer results than usual" with no explanation is the kind of silence
that wastes an afternoon.

## Deploying to Render

`render.yaml` defines both services. The ordering trap: Render only assigns a
URL after the first deploy, and each side needs the other's.

1. Deploy both from the blueprint.
2. Set `VITE_API_URL` on the frontend to the backend's URL.
3. Set `ALLOWED_ORIGINS` on the backend to the frontend's URL.
4. Redeploy the frontend — Vite bakes `VITE_*` in at build time, so a backend
   deploy will not pick it up.

Python is pinned to 3.12.11. Unpinned, Render builds against whatever is
newest, and bleeding-edge native wheels are how the sibling project ended up
crash-looping on a corrupted glibc heap.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `DATA_DIR` | `backend/data` | where the profile, matches and tracker are written |
| `ALLOWED_ORIGINS` | localhost:5173 | comma-separated CORS origins |
| `JOBS_PER_SOURCE` | `40` | postings pulled per board per scan |
| `MIN_MATCH_SCORE` | `45` | below this a job is not stored at all |
| `AUTO_PREPARE_ABOVE` | `75` | scans pre-draft letters for these |
| `MAX_PREPARE_PER_SCAN` | `5` | budget, since each is a model call |
| `MAX_JOB_AGE_DAYS` | `30` | older postings are dropped |
| `SCAN_INTERVAL_SECONDS` | `0` | in-process timer; 0 disables it |
| `LETTER_MODEL` | `openai-fast` | Pollinations text model |
| `PUBLIC_BASE_URL` | the Render URL | used in logs and summaries |

Everything in the middle block is also editable from the UI at runtime, which
is the better place to tune it.

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | status, storage durability, config |
| `GET` | `/api/sources` | every board, enabled or not |
| `POST` | `/api/resume` | upload and parse (multipart) |
| `GET` | `/api/profile` | parsed profile + the search query it produces |
| `PUT` | `/api/profile` | correct skills, titles, seniority, years |
| `DELETE` | `/api/resume` | remove resume and matches; keeps applications |
| `GET` / `PUT` | `/api/settings` | runtime search settings |
| `POST` | `/api/scan` | search, score, store, draft |
| `GET` | `/api/matches` | the last scan's results, paged |
| `GET` | `/api/applications` | the tracker |
| `POST` | `/api/applications/{id}/prepare` | draft a cover letter |
| `POST` | `/api/applications/{id}/state` | move it along |

### Application states

```
matched -> prepared -> opened -> applied
                   \-> skipped
                   \-> closed
```

`opened` is set by the app, because it opened the link. **`applied` is only
ever set by you.** Inferring it from a click would corrupt the one record
that has to be trustworthy — "where have I actually applied?" is the question
this whole thing exists to answer.

## Things to know

- **The parsed profile is guesswork and it is editable for a reason.** Skills,
  titles, seniority and years all come out of unstructured text. A wrong skill
  list silently ruins every match that follows, and you can fix in seconds
  what no parser gets right every time.
- **Scanned PDFs have no text.** An image-only PDF yields nothing and the
  upload is rejected with that explanation rather than an empty profile.
  Export a text PDF or upload a `.docx`.
- **Cover letters are drafts.** The prompt forbids inventing employers,
  dates, metrics and certifications, and tells the model to write a shorter
  letter when the overlap is thin rather than padding it. It is still working
  from a parsed resume and a scraped description, so read it before you send
  it.
- **Letters degrade rather than break.** The model is free and keyless, so it
  throttles. When it does, a plain template is used instead and the UI says
  which produced each letter.
- **The same opening appears on several boards.** De-duplication keys on
  company plus normalised title, preferring the copy with the fullest
  description, since that scores better and drafts a better letter.
- **Your resume never leaves the machine it runs on**, beyond what goes into a
  cover letter prompt. What reaches the job boards is the search query.
  `backend/data/` is gitignored for the same reason.
