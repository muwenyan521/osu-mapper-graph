"""BFS crawler for the ranked osu!standard mapper graph."""

from __future__ import annotations

import logging
from typing import Protocol, final

import anyio

from .database import Database
from .errors import ApiError, DataError
from .models import Beatmapset, CrawlStats, Difficulty, MapperJob, Owner, User, UserTag

LOGGER = logging.getLogger(__name__)
CANONICAL_CONCURRENCY = 4


def _is_ranked(value: str) -> bool:
    return value.lower() == "ranked"


def _is_osu(difficulty: Difficulty) -> bool:
    return difficulty.mode == "osu"


class ApiProtocol(Protocol):
    """Subset of the API client required by the crawler."""

    async def user_beatmapsets(self, user_id: int, kind: str) -> list[Beatmapset]: ...

    async def beatmapset(self, mapset_id: int) -> Beatmapset: ...

    async def beatmaps(self, beatmap_ids: list[int]) -> list[Difficulty]: ...

    async def tags(self) -> list[UserTag]: ...

    async def user(self, username_or_id: str | int) -> User: ...


@final
class Crawler:
    """Expand mapper participation until no new mapper remains."""

    def __init__(
        self,
        api: ApiProtocol,
        database: Database,
        *,
        max_depth: int | None = None,
        min_depth: int | None = None,
        collect_user_tags: bool = True,
        discover_mappers: bool = True,
    ) -> None:
        self._api: ApiProtocol = api
        self._database: Database = database
        self._max_depth: int | None = max_depth
        self._min_depth: int | None = min_depth
        self._processed: int = 0
        self._failures: int = 0
        self._tag_catalog: tuple[UserTag, ...] | None = None
        self._collect_user_tags: bool = collect_user_tags
        self._discover_mappers: bool = discover_mappers

    async def run(self, *, retry_failed: bool = False) -> CrawlStats:
        """Process pending mappers and return run counters."""
        while True:
            job = self._database.claim_next(retry_failed, self._max_depth, self._min_depth)
            retry_failed = False
            if job is None:
                break
            try:
                await self._process_mapper(job)
            except (ApiError, DataError) as exc:
                self._database.mark_failed(job.mapper_id, str(exc))
                self._failures += 1
                LOGGER.error("mapper %s failed: %s", job.username, exc)
                continue
            self._database.mark_done(job.mapper_id)
            self._processed += 1

        counts = self._database.counts()
        return CrawlStats(
            processed_mappers=self._processed,
            discovered_mappers=counts["mappers"],
            ranked_mapsets=counts["mapsets"],
            ranked_difficulties=counts["difficulties"],
            failures=self._failures,
        )

    async def hydrate_pending_tags(self) -> tuple[int, int]:
        """Hydrate tags for stored mapsets without expanding mapper relationships."""
        tag_catalog = {tag.id: tag for tag in await self._api.tags()}
        difficulty_ids = self._database.tags.untagged_difficulty_ids()
        processed_difficulties = 0
        processed_mapsets: set[int] = set()
        for start in range(0, len(difficulty_ids), 50):
            details = await self._api.beatmaps(difficulty_ids[start : start + 50])
            mapset_ids = {difficulty.beatmapset_id for difficulty in details}
            self._database.tags.clear_for_mapsets(mapset_ids)
            for difficulty in details:
                for tag_count in difficulty.top_tag_ids:
                    self._database.tags.add(
                        difficulty.id,
                        tag_catalog.get(tag_count.tag_id),
                        tag_count.tag_id,
                        tag_count.count,
                    )
                processed_mapsets.add(difficulty.beatmapset_id)
                processed_difficulties += 1
            self._database.tags.mark_fetched(mapset_ids)
            self._database.commit()
        return processed_difficulties, len(processed_mapsets)

    async def ingest_mapset_seeds(self, mapset_ids: list[int]) -> tuple[int, int]:
        """Ingest mapsets and promote their host/guest owners as depth-zero seeds."""
        unique_ids = list(dict.fromkeys(mapset_ids))
        candidates = [
            mapset_id for mapset_id in unique_ids if not self._database.mapset_exists(mapset_id)
        ]
        for mapset in await self._fetch_canonical_mapsets(
            [Beatmapset(id=mapset_id, status="ranked") for mapset_id in candidates]
        ):
            host_id = mapset.user_id
            host_name = mapset.creator
            if host_id is None:
                host = await self._api.user(mapset.creator)
                host_id, host_name = host.id, host.username
                canonical_mapset = mapset.model_copy(update={"user_id": host_id})
            else:
                canonical_mapset = mapset
            await self._store_mapset(
                MapperJob(host_id, host_name, 0),
                canonical_mapset,
            )
        seed_ids = self._database.promote_mapset_participants(unique_ids)
        return len(candidates), len(seed_ids)

    async def _process_mapper(self, job: MapperJob) -> None:
        listings: dict[str, list[Beatmapset]] = {}
        listing_errors: list[ApiError] = []

        async def fetch_listing(kind: str) -> None:
            try:
                listings[kind] = await self._api.user_beatmapsets(job.mapper_id, kind)
            except ApiError as exc:
                listing_errors.append(exc)

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(fetch_listing, "ranked")
            task_group.start_soon(fetch_listing, "guest")
        if listing_errors:
            raise listing_errors[0]
        mapsets = listings["ranked"] + listings["guest"]
        unique: dict[int, Beatmapset] = {mapset.id: mapset for mapset in mapsets}
        candidates = [
            mapset
            for mapset in unique.values()
            if _is_ranked(mapset.status) and not self._database.mapset_exists(mapset.id)
        ]
        all_ids = [difficulty.id for mapset in candidates for difficulty in mapset.beatmaps]
        details = await self._api.beatmaps(all_ids) if all_ids else []
        detail_by_mapset: dict[int, list[Difficulty]] = {}
        for difficulty in details:
            detail_by_mapset.setdefault(difficulty.beatmapset_id, []).append(difficulty)
        if self._collect_user_tags and self._tag_catalog is None:
            self._tag_catalog = tuple(await self._api.tags())
        canonical_mapsets = [
            mapset.model_copy(
                update={
                    "beatmaps": tuple(detail_by_mapset.get(mapset.id, [])),
                    "related_tags": self._tag_catalog or (),
                }
            )
            for mapset in candidates
            if detail_by_mapset.get(mapset.id)
        ]
        missing = [mapset for mapset in candidates if not detail_by_mapset.get(mapset.id)]
        canonical_mapsets.extend(await self._fetch_canonical_mapsets(missing))
        for canonical in canonical_mapsets:
            await self._store_mapset(job, canonical)

    async def _fetch_canonical_mapsets(self, mapsets: list[Beatmapset]) -> list[Beatmapset]:
        """Fetch mapset details concurrently while the API client globally rate-limits starts."""
        fetched: dict[int, Beatmapset] = {}
        limiter = anyio.CapacityLimiter(CANONICAL_CONCURRENCY)
        lock = anyio.Lock()

        async def fetch_one(mapset: Beatmapset) -> None:
            async with limiter:
                canonical = await self._api.beatmapset(mapset.id)
            async with lock:
                fetched[mapset.id] = canonical

        async with anyio.create_task_group() as task_group:
            for mapset in mapsets:
                task_group.start_soon(fetch_one, mapset)
        return [fetched[mapset.id] for mapset in mapsets]

    async def _store_mapset(self, job: MapperJob, mapset: Beatmapset) -> None:
        ranked_maps = [
            difficulty
            for difficulty in mapset.beatmaps
            if _is_ranked(difficulty.status) and _is_osu(difficulty)
        ]
        if not ranked_maps:
            return

        related_tags = {tag.id: tag for tag in mapset.related_tags}

        missing_owner_ids = [difficulty.id for difficulty in ranked_maps if not difficulty.owners]
        if missing_owner_ids:
            owner_map = {
                difficulty.id: difficulty
                for difficulty in await self._api.beatmaps(missing_owner_ids)
            }
            unresolved = sorted(set(missing_owner_ids) - owner_map.keys())
            if unresolved:
                raise DataError(
                    f"beatmap owners missing for mapset {mapset.id}: {', '.join(map(str, unresolved))}",
                )
            ranked_maps = [owner_map.get(difficulty.id, difficulty) for difficulty in ranked_maps]

        host_id = mapset.user_id
        if host_id is None:
            host = await self._api.user(mapset.creator)
            host_id = host.id
            host_name = host.username
            mapset = mapset.model_copy(update={"user_id": host_id})
        else:
            host_name = mapset.creator

        host_depth = job.depth if host_id == job.mapper_id else job.depth + 1
        self._database.upsert_mapper(
            Owner(id=host_id, username=host_name),
            host_depth,
            job.mapper_id,
            "pending" if self._discover_mappers else "ignored",
        )
        self._database.upsert_mapset(mapset, user_tags_fetched=self._collect_user_tags)
        if self._collect_user_tags:
            self._database.clear_user_tags(mapset.id)
        self._database.add_participation(host_id, mapset.id, "host", host_depth, job.mapper_id)
        for difficulty in ranked_maps:
            self._database.add_difficulty(difficulty)
            if self._collect_user_tags:
                for tag_count in difficulty.top_tag_ids:
                    self._database.tags.add(
                        difficulty.id,
                        related_tags.get(tag_count.tag_id),
                        tag_count.tag_id,
                        tag_count.count,
                    )
            for owner in difficulty.owners:
                owner_depth = job.depth if owner.id == job.mapper_id else job.depth + 1
                self._database.add_owner(
                    difficulty.id,
                    owner,
                    owner_depth,
                    job.mapper_id,
                    enqueue=self._discover_mappers,
                )
                role = "host" if owner.id == host_id else "guest"
                self._database.add_participation(
                    owner.id, mapset.id, role, owner_depth, job.mapper_id
                )
        self._database.commit()
