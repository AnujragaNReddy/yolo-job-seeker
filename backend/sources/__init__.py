"""The source registry.

build_sources() returns every adapter, enabled or not, so the UI can show what
is available and what a key would unlock. Callers that only want working ones
filter on enabled().
"""

import os

from .base import Job, JobSource, SourceError, clean_text
from .keyed import AdzunaSource, JoobleSource, USAJobsSource
from .keyless import (
    ArbeitnowSource,
    GreenhouseSource,
    LeverSource,
    RemotiveSource,
    TheMuseSource,
)

__all__ = [
    "Job", "JobSource", "SourceError", "clean_text",
    "build_sources", "describe_sources",
]


def _companies(env_var: str) -> list[str]:
    return [c.strip() for c in os.environ.get(env_var, "").split(",") if c.strip()]


def build_sources() -> list[JobSource]:
    return [
        RemotiveSource(),
        ArbeitnowSource(),
        TheMuseSource(),
        GreenhouseSource(_companies("GREENHOUSE_COMPANIES")),
        LeverSource(_companies("LEVER_COMPANIES")),
        AdzunaSource(),
        JoobleSource(),
        USAJobsSource(),
    ]


def enabled_sources() -> list[JobSource]:
    return [source for source in build_sources() if source.enabled()]


def describe_sources() -> list[dict]:
    return [source.describe() for source in build_sources()]
