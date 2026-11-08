"""Graph depth and crawl progress calculations."""

from __future__ import annotations

import sqlite3
from collections import deque
from typing import Protocol


class DatabaseLike(Protocol):
    """Database operations needed by progress calculations."""

    def participation_rows(self) -> list[sqlite3.Row]: ...

    def set_depths(self, depths: dict[int, int]) -> None: ...

    def scalar(self, statement: str) -> int: ...


def recompute_depths(database: DatabaseLike, seed_ids: list[int]) -> None:
    """Recompute shortest mapper depths from stored mapset participation."""
    mapset_members: dict[int, set[int]] = {}
    for row in database.participation_rows():
        mapset_members.setdefault(row["mapset_id"], set()).add(row["mapper_id"])
    mapper_mapsets: dict[int, set[int]] = {}
    for mapset_id, members in mapset_members.items():
        for mapper_id in members:
            mapper_mapsets.setdefault(mapper_id, set()).add(mapset_id)

    depths: dict[int, int] = dict.fromkeys(seed_ids, 0)
    queue = deque(seed_ids)
    while queue:
        mapper_id = queue.popleft()
        for mapset_id in mapper_mapsets.get(mapper_id, set()):
            for member_id in mapset_members[mapset_id]:
                if member_id not in depths:
                    depths[member_id] = depths[mapper_id] + 1
                    queue.append(member_id)
    database.set_depths(depths)


def collect_counts(database: DatabaseLike) -> dict[str, int]:
    """Return compact progress counts."""
    return {
        "mappers": database.scalar("SELECT COUNT(*) FROM mappers"),
        "done": database.scalar("SELECT COUNT(*) FROM mappers WHERE state='done'"),
        "pending": database.scalar("SELECT COUNT(*) FROM mappers WHERE state='pending'"),
        "failed": database.scalar("SELECT COUNT(*) FROM mappers WHERE state='failed'"),
        "mapsets": database.scalar("SELECT COUNT(*) FROM mapsets"),
        "difficulties": database.scalar("SELECT COUNT(*) FROM difficulties"),
    }
