from __future__ import annotations

from pathlib import Path

import anyio

from osu_mapper_graph.crawler import Crawler
from osu_mapper_graph.database import Database
from osu_mapper_graph.exporter import export_csv
from osu_mapper_graph.models import Beatmapset, Difficulty, Owner, TagCount, User, UserTag


class FakeApi:
    users: dict[str, User]
    sets: dict[int, Beatmapset]

    def __init__(self) -> None:
        self.users = {
            "Sotarks": User(id=1, username="Sotarks"),
            "GuestA": User(id=2, username="GuestA"),
            "CollabB": User(id=3, username="CollabB"),
            "HostC": User(id=4, username="HostC"),
        }
        self.sets = {
            10: Beatmapset(
                id=10,
                status="ranked",
                creator="Sotarks",
                user_id=1,
                artist="A",
                title="Song",
                related_tags=(UserTag(id=501, name="jumps"),),
                beatmaps=(
                    Difficulty(
                        id=100,
                        beatmapset_id=10,
                        mode="osu",
                        status="ranked",
                        version="Insane",
                        owners=(Owner(id=1, username="Sotarks"), Owner(id=2, username="GuestA")),
                        top_tag_ids=(TagCount(tag_id=501, count=7),),
                    ),
                ),
            ),
            20: Beatmapset(
                id=20,
                status="ranked",
                creator="HostC",
                user_id=4,
                artist="B",
                title="Collab",
                beatmaps=(
                    Difficulty(
                        id=200,
                        beatmapset_id=20,
                        mode="osu",
                        status="ranked",
                        version="Expert",
                        owners=(Owner(id=2, username="GuestA"), Owner(id=3, username="CollabB")),
                    ),
                ),
            ),
        }

    async def user(self, username_or_id: str | int) -> User:
        if isinstance(username_or_id, int):
            return next(user for user in self.users.values() if user.id == username_or_id)
        return self.users[username_or_id]

    async def user_beatmapsets(self, user_id: int, kind: str) -> list[Beatmapset]:
        if user_id == 1 and kind == "ranked":
            return [self.sets[10]]
        if user_id == 2 and kind == "guest":
            return [self.sets[10], self.sets[20]]
        if user_id == 4 and kind == "ranked":
            return [self.sets[20]]
        return []

    async def beatmapset(self, mapset_id: int) -> Beatmapset:
        return self.sets[mapset_id]

    async def beatmaps(self, beatmap_ids: list[int]) -> list[Difficulty]:
        return [
            difficulty
            for beatmapset in self.sets.values()
            for difficulty in beatmapset.beatmaps
            if difficulty.id in beatmap_ids
        ]

    async def tags(self) -> list[UserTag]:
        return [UserTag(id=501, name="jumps")]


def test_crawler_closes_over_guest_and_collab_owners(tmp_path: Path) -> None:
    db = Database(tmp_path / "graph.sqlite3")
    try:
        anyio.run(_run_crawl, db)
        counts = db.counts()
        assert counts["mappers"] == 4
        assert counts["mapsets"] == 2
        assert counts["difficulties"] == 2
        output = tmp_path / "catalog.csv"
        assert export_csv(db, output) == 5
        lines = output.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 6
        assert "difficulty_owner_names" in lines[0]
        assert "user_tag_names" in lines[0]
        assert "jumps" in "\n".join(lines)
        assert "GuestA,CollabB" in "\n".join(lines)
    finally:
        db.close()


async def _run_crawl(db: Database) -> None:
    api = FakeApi()
    db.seed(api.users["Sotarks"])
    _ = await Crawler(api, db).run()


def test_max_depth_leaves_boundary_mappers_pending(tmp_path: Path) -> None:
    db = Database(tmp_path / "depth.sqlite3")
    try:
        anyio.run(_run_depth_limited, db)
        counts = db.counts()
        assert counts["done"] == 1
        assert counts["pending"] == 1
    finally:
        db.close()


async def _run_depth_limited(db: Database) -> None:
    api = FakeApi()
    db.seed(api.users["Sotarks"])
    _ = await Crawler(api, db, max_depth=1).run()


def test_crawler_can_skip_user_tags_for_fast_relationship_pass(tmp_path: Path) -> None:
    db = Database(tmp_path / "skip-tags.sqlite3")
    try:
        anyio.run(_run_without_tags, db)
        assert db.scalar("SELECT COUNT(*) FROM difficulty_user_tags") == 0
        assert db.scalar("SELECT user_tags_fetched FROM mapsets") == 0
    finally:
        db.close()


async def _run_without_tags(db: Database) -> None:
    api = FakeApi()
    db.seed(api.users["Sotarks"])
    _ = await Crawler(api, db, collect_user_tags=False).run()


def test_prepare_seed_batch_requeues_existing_done_mapper(tmp_path: Path) -> None:
    db = Database(tmp_path / "seed-batch.sqlite3")
    try:
        user = User(id=9, username="Existing")
        db.seed(user)
        db.mark_done(user.id)
        db.prepare_seed_batch([user])
        _, rows = db.query_rows(
            "SELECT first_depth, state, discovered_from FROM mappers WHERE id=9"
        )
        row = rows[0]
        assert tuple(row) == (0, "pending", None)
    finally:
        db.close()


def test_promote_mapset_participants_requeues_all_known_owners(tmp_path: Path) -> None:
    db = Database(tmp_path / "mapset-seed.sqlite3")
    try:
        anyio.run(_run_crawl, db)
        ids = db.promote_mapset_participants([10])
        assert 1 in ids
        assert 2 in ids
        _, rows = db.query_rows(
            "SELECT state, first_depth FROM mappers WHERE id IN (1, 2) ORDER BY id"
        )
        assert [tuple(row) for row in rows] == [("pending", 0), ("pending", 0)]
    finally:
        db.close()
