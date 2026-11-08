from __future__ import annotations

import csv
from pathlib import Path

from osu_mapper_graph.database import Database
from osu_mapper_graph.exporter import export_csv
from osu_mapper_graph.models import Beatmapset, Difficulty, User, UserTag


def test_tagged_ranked_osu_difficulties_are_selected_once(tmp_path: Path) -> None:
    database = Database(tmp_path / "graph.sqlite3")
    try:
        database.upsert_mapset(
            Beatmapset(id=10, status="ranked", creator="Mapper", user_id=1),
            user_tags_fetched=True,
        )
        database.add_difficulty(_difficulty(100, "osu", "ranked"))
        database.add_difficulty(_difficulty(101, "osu", "ranked"))
        database.add_difficulty(_difficulty(102, "osu", "loved"))
        database.add_difficulty(_difficulty(103, "taiko", "ranked"))
        database.tags.add(100, UserTag(id=501, name="jumps"), 501, 7)
        database.tags.add(100, UserTag(id=502, name="tech"), 502, 4)
        database.tags.add(102, UserTag(id=503, name="streams"), 503, 2)
        database.tags.add(103, UserTag(id=504, name="aim"), 504, 1)
        database.commit()

        assert database.tagged_ranked_osu_difficulty_ids() == [100]
    finally:
        database.close()


def test_superseded_estimates_are_not_exported(tmp_path: Path) -> None:
    database = Database(tmp_path / "graph.sqlite3")
    try:
        database.seed(User(id=1, username="Mapper"))
        database.upsert_mapset(
            Beatmapset(id=10, status="ranked", creator="Mapper", user_id=1),
            user_tags_fetched=True,
        )
        database.add_difficulty(_difficulty(100, "osu", "ranked"))
        database.tags.add(100, UserTag(id=501, name="jumps"), 501, 7)
        database.add_participation(1, 10, "host", 0, 1)
        database.upsert_pp_estimate(
            100,
            source="osu_api_attributes_nm+rosu-pp-4.0.1",
            status="ok",
            pp_nm_100_fc=500.0,
            pp_nm_98_fc=470.0,
            pp_nm_95_fc=430.0,
        )
        database.commit()

        output = tmp_path / "catalog.csv"
        assert export_csv(database, output) == 1
        with output.open(newline="", encoding="utf-8") as handle:
            row = next(csv.DictReader(handle))

        assert "pp_estimate_nm_100_fc" not in row
        assert "pp_estimate_nm_98_fc" not in row
        assert "pp_estimate_nm_95_fc" not in row
    finally:
        database.close()


def _difficulty(beatmap_id: int, mode: str, status: str) -> Difficulty:
    return Difficulty(
        id=beatmap_id,
        beatmapset_id=10,
        mode=mode,
        status=status,
        version=f"Diff {beatmap_id}",
    )
