"""Typed errors raised by the crawler."""

from dataclasses import dataclass
from typing import override


@dataclass(slots=True)
class ApiError(Exception):
    """An osu! API request failed after retries."""

    method: str
    path: str
    status_code: int | None
    detail: str

    @override
    def __str__(self) -> str:
        status = f" HTTP {self.status_code}" if self.status_code is not None else ""
        return f"osu! API {self.method} {self.path}{status}: {self.detail}"


@dataclass(slots=True)
class ConfigurationError(Exception):
    """Required local configuration is missing."""

    detail: str

    @override
    def __str__(self) -> str:
        return self.detail


@dataclass(slots=True)
class DataError(Exception):
    """An API response cannot provide the data needed for graph expansion."""

    detail: str

    @override
    def __str__(self) -> str:
        return self.detail


@dataclass(slots=True)
class EstimateError(Exception):
    """Attributes-only PP estimation failed for one beatmap."""

    beatmap_id: int
    detail: str

    @override
    def __str__(self) -> str:
        return f"PP estimate for beatmap {self.beatmap_id} failed: {self.detail}"
