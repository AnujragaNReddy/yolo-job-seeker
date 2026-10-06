"""Boards that need free credentials.

All optional. Each stays switched off until its env vars are set, and
describe() says which are missing so the UI can explain the gap rather than
just showing fewer results.
"""

import os

from .base import Job, JobSource


class AdzunaSource(JobSource):
    """Broad aggregator with good salary data. Free tier, instant signup at
    developer.adzuna.com."""

    name = "adzuna"
    label = "Adzuna"
    requires_key = True

    def __init__(self):
        self.app_id = os.environ.get("ADZUNA_APP_ID", "").strip()
        self.app_key = os.environ.get("ADZUNA_APP_KEY", "").strip()
        # Adzuna is country-scoped in the URL itself, so this is not optional
        # the way a filter would be.
        self.country = os.environ.get("ADZUNA_COUNTRY", "in").strip().lower()

    def enabled(self) -> bool:
        return bool(self.app_id and self.app_key)

    def describe(self) -> dict:
        return {
            **super().describe(),
            "country": self.country,
            "missing": [] if self.enabled() else ["ADZUNA_APP_ID", "ADZUNA_APP_KEY"],
        }

    def fetch(self, query: str, limit: int = 50) -> list[Job]:
        url = f"https://api.adzuna.com/v1/api/jobs/{self.country}/search/1"

        payload = self._get(url, params={
            "app_id": self.app_id,
            "app_key": self.app_key,
            "results_per_page": max(1, min(limit, 50)),
            "what": query or "",
            "content-type": "application/json",
        })

        jobs = []

        for row in payload.get("results", []):
            low, high = row.get("salary_min"), row.get("salary_max")
            salary = ""
            if low and high:
                salary = f"{int(low):,} - {int(high):,}"

            location = (row.get("location") or {}).get("display_name", "")

            jobs.append(Job(
                source=self.name,
                title=row.get("title", ""),
                company=(row.get("company") or {}).get("display_name", ""),
                url=row.get("redirect_url", ""),
                description=row.get("description", ""),
                location=location,
                remote="remote" in f"{location} {row.get('title', '')}".lower(),
                salary=salary,
                posted_at=row.get("created", "") or "",
                tags=[(row.get("category") or {}).get("label", "")],
            ))

        return jobs


class JoobleSource(JobSource):
    """Aggregator covering many regions. Free key from jooble.org/api/about."""

    name = "jooble"
    label = "Jooble"
    requires_key = True

    def __init__(self):
        self.api_key = os.environ.get("JOOBLE_API_KEY", "").strip()
        self.location = os.environ.get("JOB_LOCATION", "").strip()

    def enabled(self) -> bool:
        return bool(self.api_key)

    def describe(self) -> dict:
        return {
            **super().describe(),
            "missing": [] if self.enabled() else ["JOOBLE_API_KEY"],
        }

    def fetch(self, query: str, limit: int = 50) -> list[Job]:
        # The only POST among the sources, so it does not use self._get.
        import requests

        from .base import SourceError, TIMEOUT_SECONDS, USER_AGENT

        try:
            response = requests.post(
                f"https://jooble.org/api/{self.api_key}",
                json={"keywords": query or "", "location": self.location},
                headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
                timeout=TIMEOUT_SECONDS,
            )
        except requests.RequestException as e:
            raise SourceError(f"Could not reach Jooble: {e}") from e

        if not response.ok:
            raise SourceError(f"Jooble returned HTTP {response.status_code}.")

        try:
            payload = response.json()
        except ValueError as e:
            raise SourceError("Jooble did not return JSON.") from e

        jobs = []

        for row in payload.get("jobs", [])[:limit]:
            location = row.get("location", "") or ""
            jobs.append(Job(
                source=self.name,
                title=row.get("title", ""),
                company=row.get("company", ""),
                url=row.get("link", ""),
                description=row.get("snippet", ""),
                location=location,
                remote="remote" in location.lower(),
                salary=row.get("salary", "") or "",
                posted_at=row.get("updated", "") or "",
            ))

        return jobs


class USAJobsSource(JobSource):
    """US federal postings. Official government API, free key, no rate pain."""

    name = "usajobs"
    label = "USAJOBS"
    requires_key = True

    ENDPOINT = "https://data.usajobs.gov/api/search"

    def __init__(self):
        self.email = os.environ.get("USAJOBS_EMAIL", "").strip()
        self.api_key = os.environ.get("USAJOBS_API_KEY", "").strip()

    def enabled(self) -> bool:
        return bool(self.email and self.api_key)

    def describe(self) -> dict:
        return {
            **super().describe(),
            "missing": [] if self.enabled() else ["USAJOBS_EMAIL", "USAJOBS_API_KEY"],
        }

    def fetch(self, query: str, limit: int = 50) -> list[Job]:
        payload = self._get(
            self.ENDPOINT,
            params={"Keyword": query or "", "ResultsPerPage": max(1, min(limit, 50))},
            # USAJOBS authenticates with a header pair rather than a token.
            headers={"Host": "data.usajobs.gov", "User-Agent": self.email,
                     "Authorization-Key": self.api_key},
        )

        jobs = []
        results = (payload.get("SearchResult") or {}).get("SearchResultItems", [])

        for item in results:
            row = item.get("MatchedObjectDescriptor") or {}
            pay = (row.get("PositionRemuneration") or [{}])[0]
            low, high = pay.get("MinimumRange"), pay.get("MaximumRange")

            locations = [
                loc.get("LocationName", "")
                for loc in (row.get("PositionLocation") or [])
            ]

            jobs.append(Job(
                source=self.name,
                title=row.get("PositionTitle", ""),
                company=row.get("OrganizationName", ""),
                url=row.get("PositionURI", ""),
                description=" ".join(filter(None, [
                    (row.get("UserArea") or {}).get("Details", {}).get("JobSummary", ""),
                    (row.get("QualificationSummary") or ""),
                ])),
                location=", ".join(l for l in locations if l),
                salary=f"{low} - {high}" if low and high else "",
                posted_at=row.get("PublicationStartDate", "") or "",
                tags=[c.get("Name", "") for c in (row.get("JobCategory") or [])],
            ))

        return jobs
