"""One job shape, whatever board it came from.

Every source returns Job objects, so matching and storage never need to know
which API produced a posting. Adding a board means writing a fetch() that
normalises into this shape and nothing else.

Sources that need no credentials come first by design — the app is useful the
moment it starts, and keys only widen the search rather than enable it.
"""

import hashlib
import html
import re
from dataclasses import asdict, dataclass, field
from typing import Optional

import requests

USER_AGENT = "yolo-job-seeker/1.0 (job search aggregator)"
TIMEOUT_SECONDS = 30

# Postings routinely carry HTML. It reaches skill matching, a cover letter
# prompt and the UI, so it gets flattened once here rather than three times
# downstream.
_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"[ \t ]+")
_BLANK_LINES = re.compile(r"\n{3,}")

# Tags that mean "new line" rather than "inline markup". Collapsing a whole
# posting into one paragraph loses the bullet structure that skill lists live
# in, which is the part worth reading.
_BLOCK_TAG = re.compile(
    r"</?(?:br|p|div|li|ul|ol|tr|h[1-6]|section|article)\b[^>]*>",
    re.IGNORECASE,
)


def clean_text(raw: Optional[str]) -> str:
    """HTML in, readable text out.

    Order and repetition both matter here, and getting either wrong is quiet.
    Some boards serve descriptions with the markup escaped, so the payload
    holds "&lt;p&gt;" rather than "<p>". Unescaping after stripping tags turns
    those back into tags once the stripper has already run, leaving markup in
    text that has supposedly been cleaned — Arbeitnow descriptions came
    through with 211 tags intact that way.

    So: unescape first, strip second, and repeat while unescaping keeps
    revealing more. Bounded, because the input is untrusted and a string
    crafted to unescape into itself would otherwise loop forever.
    """
    if not raw:
        return ""

    text = raw

    for _ in range(3):
        decoded = html.unescape(text)
        # Block-level tags carry the line structure, which matters for reading
        # and for the cover letter prompt, so they become newlines rather than
        # disappearing into one paragraph.
        decoded = _BLOCK_TAG.sub("\n", decoded)
        stripped = _TAG.sub(" ", decoded)

        if stripped == text:
            break

        text = stripped

    text = _WHITESPACE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.splitlines())

    return _BLANK_LINES.sub("\n\n", text).strip()


@dataclass
class Job:
    source: str
    title: str
    company: str
    url: str
    description: str = ""
    location: str = ""
    remote: bool = False
    salary: str = ""
    posted_at: str = ""
    tags: list[str] = field(default_factory=list)
    id: str = ""

    def __post_init__(self):
        self.title = (self.title or "").strip()
        self.company = (self.company or "").strip()
        self.location = (self.location or "").strip()
        self.description = clean_text(self.description)

        if not self.id:
            self.id = self.fingerprint()

    def fingerprint(self) -> str:
        """Stable across scans, and equal for the same role on two boards.

        Deliberately built from company and title rather than the URL: the
        same opening is listed on several aggregators with different links,
        and showing it three times would be worse than occasionally merging
        two genuinely different openings with one title at one company.
        """
        basis = f"{self.company.lower()}|{re.sub(r'[^a-z0-9]+', ' ', self.title.lower()).strip()}"
        return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict:
        return asdict(self)


class SourceError(RuntimeError):
    """The source was configured but the call failed."""


class JobSource:
    """A board this app can search."""

    name = "base"
    label = "Base"
    # False when the source works without credentials, which is what makes it
    # a sensible default.
    requires_key = False

    def enabled(self) -> bool:
        return True

    def describe(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "enabled": self.enabled(),
            "requires_key": self.requires_key,
        }

    def fetch(self, query: str, limit: int = 50) -> list[Job]:
        raise NotImplementedError

    def _get(self, url: str, params: Optional[dict] = None,
             headers: Optional[dict] = None) -> dict:
        merged = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        merged.update(headers or {})

        try:
            response = requests.get(url, params=params, headers=merged,
                                    timeout=TIMEOUT_SECONDS)
        except requests.RequestException as e:
            raise SourceError(f"Could not reach {self.label}: {e}") from e

        if not response.ok:
            raise SourceError(f"{self.label} returned HTTP {response.status_code}.")

        try:
            return response.json()
        except ValueError as e:
            raise SourceError(f"{self.label} did not return JSON.") from e
