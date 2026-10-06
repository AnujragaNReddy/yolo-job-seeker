"""Boards with open APIs that need no credentials.

These carry the app on their own. Each has a documented public JSON endpoint
intended for exactly this, so none of it involves scraping HTML or pretending
to be a browser.
"""

from typing import Optional

from .base import Job, JobSource


class RemotiveSource(JobSource):
    """Remote-only roles. Documented open API, no key, generous with detail."""

    name = "remotive"
    label = "Remotive"

    ENDPOINT = "https://remotive.com/api/remote-jobs"

    def fetch(self, query: str, limit: int = 50) -> list[Job]:
        params = {"limit": max(1, min(limit, 100))}
        if query:
            params["search"] = query

        payload = self._get(self.ENDPOINT, params=params)
        jobs = []

        for row in payload.get("jobs", [])[:limit]:
            jobs.append(Job(
                source=self.name,
                title=row.get("title", ""),
                company=row.get("company_name", ""),
                url=row.get("url", ""),
                description=row.get("description", ""),
                location=row.get("candidate_required_location", "") or "Remote",
                remote=True,
                salary=row.get("salary", "") or "",
                posted_at=row.get("publication_date", "") or "",
                tags=[t for t in (row.get("tags") or []) if t],
            ))

        return jobs


class ArbeitnowSource(JobSource):
    """Open job board API, Europe-weighted, no key."""

    name = "arbeitnow"
    label = "Arbeitnow"

    ENDPOINT = "https://www.arbeitnow.com/api/job-board-api"

    def fetch(self, query: str, limit: int = 50) -> list[Job]:
        # This endpoint has no search parameter, so it is filtered here
        # instead. Asking for a page and narrowing locally is the documented
        # way to use it.
        payload = self._get(self.ENDPOINT)
        needle = (query or "").lower().strip()
        jobs = []

        for row in payload.get("data", []):
            title = row.get("title", "")
            description = row.get("description", "")

            if needle and needle not in f"{title} {description}".lower():
                continue

            jobs.append(Job(
                source=self.name,
                title=title,
                company=row.get("company_name", ""),
                url=row.get("url", ""),
                description=description,
                location=row.get("location", "") or "",
                remote=bool(row.get("remote")),
                posted_at=str(row.get("created_at", "") or ""),
                tags=[t for t in (row.get("tags") or []) if t],
            ))

            if len(jobs) >= limit:
                break

        return jobs


class TheMuseSource(JobSource):
    """The Muse's public jobs API. A key raises the rate limit; none is
    required, so it stays in the keyless set."""

    name = "themuse"
    label = "The Muse"

    ENDPOINT = "https://www.themuse.com/api/public/jobs"

    def fetch(self, query: str, limit: int = 50) -> list[Job]:
        jobs: list[Job] = []
        needle = (query or "").lower().strip()

        # Paged, 20 per page, and the API offers no free-text search — so walk
        # a few pages and filter here, stopping as soon as there is enough.
        for page in range(0, 3):
            payload = self._get(self.ENDPOINT, params={"page": page})

            for row in payload.get("results", []):
                title = row.get("name", "")
                contents = row.get("contents", "")

                if needle and needle not in f"{title} {contents}".lower():
                    continue

                locations = [
                    loc.get("name", "") for loc in (row.get("locations") or [])
                ]
                location = ", ".join(l for l in locations if l)

                jobs.append(Job(
                    source=self.name,
                    title=title,
                    company=(row.get("company") or {}).get("name", ""),
                    url=(row.get("refs") or {}).get("landing_page", ""),
                    description=contents,
                    location=location,
                    remote=any("flexible" in l.lower() or "remote" in l.lower()
                               for l in locations),
                    posted_at=row.get("publication_date", "") or "",
                    tags=[c.get("name", "") for c in (row.get("categories") or [])],
                ))

                if len(jobs) >= limit:
                    return jobs

            if not payload.get("results"):
                break

        return jobs


class _CompanyBoardSource(JobSource):
    """Shared behaviour for per-company ATS boards.

    Greenhouse and Lever both publish a company's open roles as JSON for
    anyone to consume — it is how companies embed their own careers page. The
    catch is that it is per company, so these only do anything once there are
    company slugs to ask about.
    """

    def __init__(self, companies: Optional[list[str]] = None):
        self.companies = [c.strip() for c in (companies or []) if c.strip()]

    def enabled(self) -> bool:
        return bool(self.companies)

    def describe(self) -> dict:
        return {**super().describe(), "companies": self.companies}


class GreenhouseSource(_CompanyBoardSource):
    name = "greenhouse"
    label = "Greenhouse boards"

    def fetch(self, query: str, limit: int = 50) -> list[Job]:
        needle = (query or "").lower().strip()
        jobs: list[Job] = []

        for company in self.companies:
            if len(jobs) >= limit:
                break

            url = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs"

            try:
                payload = self._get(url, params={"content": "true"})
            except Exception:
                # One misspelled slug shouldn't take out the others.
                continue

            for row in payload.get("jobs", []):
                title = row.get("title", "")
                content = row.get("content", "")

                if needle and needle not in f"{title} {content}".lower():
                    continue

                jobs.append(Job(
                    source=self.name,
                    title=title,
                    company=company,
                    url=row.get("absolute_url", ""),
                    description=content,
                    location=(row.get("location") or {}).get("name", ""),
                    posted_at=row.get("updated_at", "") or "",
                ))

                if len(jobs) >= limit:
                    break

        return jobs


class LeverSource(_CompanyBoardSource):
    name = "lever"
    label = "Lever boards"

    def fetch(self, query: str, limit: int = 50) -> list[Job]:
        needle = (query or "").lower().strip()
        jobs: list[Job] = []

        for company in self.companies:
            if len(jobs) >= limit:
                break

            url = f"https://api.lever.co/v0/postings/{company}"

            try:
                payload = self._get(url, params={"mode": "json"})
            except Exception:
                continue

            # Lever answers with a bare list, unlike everything else here.
            rows = payload if isinstance(payload, list) else payload.get("data", [])

            for row in rows:
                title = row.get("text", "")
                description = row.get("descriptionPlain") or row.get("description", "")

                if needle and needle not in f"{title} {description}".lower():
                    continue

                categories = row.get("categories") or {}
                location = categories.get("location", "") or ""

                jobs.append(Job(
                    source=self.name,
                    title=title,
                    company=company,
                    url=row.get("hostedUrl", "") or row.get("applyUrl", ""),
                    description=description,
                    location=location,
                    remote="remote" in location.lower(),
                    tags=[v for v in (categories.get("commitment"),
                                      categories.get("team")) if v],
                ))

                if len(jobs) >= limit:
                    break

        return jobs
