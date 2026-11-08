"""Typed API and crawler models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    """Base model that tolerates fields added by osu! API v2."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)


class Owner(ApiModel):
    """A mapper owning one difficulty."""

    id: int
    username: str


class TagCount(ApiModel):
    """A user tag and its displayed vote count on one difficulty."""

    tag_id: int
    count: int


class UserTag(ApiModel):
    """User-curated tag metadata shared by a beatmapset response."""

    id: int
    name: str
    ruleset_id: int | None = None
    description: str = ""


class User(ApiModel):
    """A user returned by the osu! API."""

    id: int
    username: str


class Difficulty(ApiModel):
    """A beatmap difficulty, including optional mapper ownership."""

    id: int
    beatmapset_id: int
    mode: str
    status: str
    version: str
    difficulty_rating: float | None = None
    ar: float | None = None
    cs: float | None = None
    od: float | None = Field(default=None, validation_alias="accuracy")
    drain: float | None = None
    bpm: float | None = None
    total_length: int | None = None
    hit_length: int | None = None
    count_circles: int | None = None
    count_sliders: int | None = None
    count_spinners: int | None = None
    owners: tuple[Owner, ...] = Field(default_factory=tuple)
    top_tag_ids: tuple[TagCount, ...] = Field(default_factory=tuple)


class Beatmapset(ApiModel):
    """A beatmapset returned by a user beatmap listing."""

    id: int
    status: str
    artist: str = ""
    artist_unicode: str = ""
    title: str = ""
    title_unicode: str = ""
    creator: str = ""
    user_id: int | None = None
    source: str = ""
    tags: str = ""
    ranked_date: str | None = None
    submitted_date: str | None = None
    last_updated: str | None = None
    beatmaps: tuple[Difficulty, ...] = Field(default_factory=tuple)
    related_tags: tuple[UserTag, ...] = Field(default_factory=tuple)


class BeatmapAttributes(ApiModel):
    """Difficulty attributes returned by the osu! API attributes endpoint."""

    star_rating: float
    max_combo: int
    aim_difficulty: float
    aim_difficult_slider_count: float
    speed_difficulty: float
    speed_note_count: float
    flashlight_difficulty: float = 0.0
    slider_factor: float
    aim_top_weighted_slider_factor: float = 0.0
    speed_top_weighted_slider_factor: float = 0.0
    aim_difficult_strain_count: float
    speed_difficult_strain_count: float
    nested_score_per_object: float = 0.0
    legacy_score_base_multiplier: float = 0.0
    maximum_legacy_combo_score: float = 0.0


class BeatmapAttributesResponse(ApiModel):
    """Envelope returned by POST /api/v2/beatmaps/{id}/attributes."""

    attributes: BeatmapAttributes


class BeatmapPpMetadata(ApiModel):
    """API fields needed to complete osu!standard difficulty attributes."""

    ar: float
    od: float
    hp: float
    count_circles: int
    count_sliders: int
    count_spinners: int


class BeatmapPpEstimate(ApiModel):
    """Attributes-only PP output for NM full-combo scenarios."""

    pp_nm_100_fc: float
    pp_nm_98_fc: float
    pp_nm_95_fc: float


@dataclass(frozen=True, slots=True)
class MapperJob:
    """A mapper queued for graph expansion."""

    mapper_id: int
    username: str
    depth: int


@dataclass(frozen=True, slots=True)
class CrawlStats:
    """Counters emitted after a crawl run."""

    processed_mappers: int
    discovered_mappers: int
    ranked_mapsets: int
    ranked_difficulties: int
    failures: int
