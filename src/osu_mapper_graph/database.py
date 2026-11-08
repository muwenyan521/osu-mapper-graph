"""SQLite persistence for resumable graph crawling."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import final

from .models import Beatmapset, Difficulty, MapperJob, Owner, User
from .progress import collect_counts, recompute_depths
from .schema import SCHEMA
from .seed_store import prepare_batch, promote_mapset_participants, refresh_user_tags
from .tag_store import TagStore


@final
class Database:
    """Own the SQLite connection and graph upserts."""

    def __init__(self, path: Path, *, recover_processing: bool = False) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection: sqlite3.Connection = sqlite3.connect(path)
        self._connection.row_factory = sqlite3.Row
        self._connection.executescript(SCHEMA)
        self.tags: TagStore = TagStore(self._connection)
        self._ensure_user_tag_column()
        self._ensure_difficulty_count_columns()
        if recover_processing:
            self._recover_processing()

    def _ensure_user_tag_column(self) -> None:
        """Migrate databases created before user-tag support."""
        try:
            self._connection.execute(
                "ALTER TABLE mapsets ADD COLUMN user_tags_fetched INTEGER NOT NULL DEFAULT 0"
            )
            self._connection.commit()
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise

    def _ensure_difficulty_count_columns(self) -> None:
        """Migrate databases created before JSON beatmap counts were stored."""
        for column in ("count_circles", "count_sliders", "count_spinners"):
            try:
                self._connection.execute(f"ALTER TABLE difficulties ADD COLUMN {column} INTEGER")
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
        self._connection.commit()

    def close(self) -> None:
        """Close the database connection."""
        self._connection.close()

    def _recover_processing(self) -> None:
        """Return jobs left processing by an interrupted process to the queue."""
        self._connection.execute("UPDATE mappers SET state='pending' WHERE state='processing'")
        self._connection.commit()

    def upsert_mapper(
        self,
        user: User | Owner,
        depth: int,
        discovered_from: int | None,
        state: str = "pending",
    ) -> None:
        """Insert a mapper without downgrading a completed job."""
        self._connection.execute(
            """
            INSERT INTO mappers (id, username, first_depth, discovered_from, state)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                username = excluded.username,
                first_depth = MIN(mappers.first_depth, excluded.first_depth),
                discovered_from = COALESCE(mappers.discovered_from, excluded.discovered_from)
            """,
            (user.id, user.username, depth, discovered_from, state),
        )

    def seed(self, user: User) -> None:
        """Insert a depth-zero seed mapper."""
        self.upsert_mapper(user, 0, None)
        self._connection.commit()

    def prepare_seed_batch(self, users: list[User]) -> None:
        prepare_batch(self._connection, users)

    def promote_mapset_participants(self, mapset_ids: list[int]) -> list[int]:
        return promote_mapset_participants(self._connection, mapset_ids)

    def refresh_user_tags(self) -> None:
        refresh_user_tags(self._connection)

    def recompute_depths(self, seed_ids: list[int]) -> None:
        """Recompute shortest mapper depths from stored mapset participation."""
        recompute_depths(self, seed_ids)

    def participation_rows(self) -> list[sqlite3.Row]:
        """Return mapper-to-mapset edges for graph calculations."""
        return list(self._connection.execute("SELECT mapper_id, mapset_id FROM mapper_mapsets"))

    def set_depths(self, depths: dict[int, int]) -> None:
        """Persist recomputed mapper depths."""
        for mapper_id, depth in depths.items():
            self._connection.execute(
                "UPDATE mappers SET first_depth=? WHERE id=?", (depth, mapper_id)
            )
        self._connection.commit()

    def scalar(self, statement: str) -> int:
        """Return the integer result of a scalar report query."""
        return int(self._connection.execute(statement).fetchone()[0])

    def claim_next(
        self,
        retry_failed: bool,
        max_depth: int | None = None,
        min_depth: int | None = None,
    ) -> MapperJob | None:
        """Atomically claim the next pending mapper."""
        if retry_failed:
            self._connection.execute("UPDATE mappers SET state='pending' WHERE state='failed'")
            self._connection.commit()
        row = self._connection.execute(
            """
            SELECT id, username, first_depth
            FROM mappers
            WHERE state='pending'
              AND (? IS NULL OR first_depth < ?)
              AND (? IS NULL OR first_depth >= ?)
            ORDER BY first_depth, id LIMIT 1
            """,
            (max_depth, max_depth, min_depth, min_depth),
        ).fetchone()
        if row is None:
            return None
        self._connection.execute(
            "UPDATE mappers SET state='processing', attempts=attempts+1 WHERE id=?",
            (row["id"],),
        )
        self._connection.commit()
        return MapperJob(row["id"], row["username"], row["first_depth"])

    def mark_done(self, mapper_id: int) -> None:
        """Mark a mapper successfully expanded."""
        self._connection.execute(
            "UPDATE mappers SET state='done', processed_at=CURRENT_TIMESTAMP, last_error=NULL WHERE id=?",
            (mapper_id,),
        )
        self._connection.commit()

    def mark_failed(self, mapper_id: int, detail: str) -> None:
        """Retain a failed mapper for an explicit retry."""
        self._connection.execute(
            "UPDATE mappers SET state='failed', last_error=? WHERE id=?",
            (detail[:2000], mapper_id),
        )
        self._connection.commit()

    def mapset_exists(self, mapset_id: int) -> bool:
        """Return whether a mapset has already been hydrated."""
        return (
            self._connection.execute(
                "SELECT 1 FROM mapsets WHERE id=? AND user_tags_fetched=1",
                (mapset_id,),
            ).fetchone()
            is not None
        )

    def upsert_mapset(self, mapset: Beatmapset, *, user_tags_fetched: bool = True) -> None:
        """Store mapset metadata."""
        self._connection.execute(
            """
            INSERT INTO mapsets
                (id, status, artist, artist_unicode, title, title_unicode, creator, creator_id,
                 source, tags, ranked_date, submitted_date, last_updated, user_tags_fetched)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status, artist=excluded.artist, artist_unicode=excluded.artist_unicode,
                title=excluded.title, title_unicode=excluded.title_unicode, creator=excluded.creator,
                creator_id=excluded.creator_id, source=excluded.source, tags=excluded.tags,
                ranked_date=excluded.ranked_date, submitted_date=excluded.submitted_date,
                last_updated=excluded.last_updated, user_tags_fetched=excluded.user_tags_fetched,
                fetched_at=CURRENT_TIMESTAMP
            """,
            (
                mapset.id,
                mapset.status,
                mapset.artist,
                mapset.artist_unicode,
                mapset.title,
                mapset.title_unicode,
                mapset.creator,
                mapset.user_id,
                mapset.source,
                mapset.tags,
                mapset.ranked_date,
                mapset.submitted_date,
                mapset.last_updated,
                int(user_tags_fetched),
            ),
        )

    def add_difficulty(self, difficulty: Difficulty) -> None:
        """Store one difficulty's metadata."""
        self._connection.execute(
            """
            INSERT INTO difficulties
                (id, mapset_id, mode, status, version, star_rating, ar, cs, od, hp, bpm,
                total_length, hit_length, count_circles, count_sliders, count_spinners)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                mapset_id=excluded.mapset_id, mode=excluded.mode, status=excluded.status,
                version=excluded.version, star_rating=excluded.star_rating, ar=excluded.ar,
                cs=excluded.cs, od=excluded.od, hp=excluded.hp, bpm=excluded.bpm,
                total_length=excluded.total_length, hit_length=excluded.hit_length,
                count_circles=excluded.count_circles, count_sliders=excluded.count_sliders,
                count_spinners=excluded.count_spinners
            """,
            (
                difficulty.id,
                difficulty.beatmapset_id,
                difficulty.mode,
                difficulty.status,
                difficulty.version,
                difficulty.difficulty_rating,
                difficulty.ar,
                difficulty.cs,
                difficulty.od,
                difficulty.drain,
                difficulty.bpm,
                difficulty.total_length,
                difficulty.hit_length,
                difficulty.count_circles,
                difficulty.count_sliders,
                difficulty.count_spinners,
            ),
        )

    def tagged_ranked_osu_difficulty_ids(self) -> list[int]:
        """Return each ranked osu! difficulty that has at least one user tag."""
        rows = self._connection.execute(
            """
            SELECT d.id
            FROM difficulties d
            WHERE d.mode='osu' AND lower(d.status)='ranked'
              AND EXISTS (
                  SELECT 1 FROM difficulty_user_tags ut
                  WHERE ut.difficulty_id=d.id
              )
            ORDER BY d.id
            """
        )
        return [int(row[0]) for row in rows]

    def tagged_ranked_osu_difficulty_rows(self) -> list[sqlite3.Row]:
        """Return tagged ranked osu! difficulties and stored metadata."""
        return list(
            self._connection.execute(
                """
                SELECT d.id, d.ar, d.od, d.hp, d.count_circles, d.count_sliders,
                       d.count_spinners
                FROM difficulties d
                WHERE d.mode='osu' AND lower(d.status)='ranked'
                  AND EXISTS (
                      SELECT 1 FROM difficulty_user_tags ut
                      WHERE ut.difficulty_id=d.id
                  )
                ORDER BY d.id
                """
            )
        )

    def pending_pp_estimate_rows(
        self, *, force: bool = False, limit: int | None = None
    ) -> list[sqlite3.Row]:
        """Return tagged ranked osu! difficulties without a successful estimate."""
        params = (int(force), -1 if limit is None else limit)
        return list(
            self._connection.execute(
                """
                SELECT d.id, d.ar, d.od, d.hp, d.count_circles, d.count_sliders,
                       d.count_spinners
                FROM difficulties d
                LEFT JOIN beatmap_pp_estimates e ON e.beatmap_id=d.id
                WHERE d.mode='osu' AND lower(d.status)='ranked'
                  AND EXISTS (
                      SELECT 1 FROM difficulty_user_tags ut
                      WHERE ut.difficulty_id=d.id
                  )
                  AND (? = 1 OR COALESCE(e.status, '') <> 'ok')
                ORDER BY d.id
                LIMIT ?
                """,
                params,
            )
        )

    def upsert_pp_estimate(
        self,
        beatmap_id: int,
        *,
        source: str,
        status: str,
        pp_nm_100_fc: float | None = None,
        pp_nm_98_fc: float | None = None,
        pp_nm_95_fc: float | None = None,
        attribute_star_rating: float | None = None,
        attribute_max_combo: int | None = None,
        error: str | None = None,
    ) -> None:
        """Persist one resumable attributes-only PP estimate."""
        self._connection.execute(
            """
            INSERT INTO beatmap_pp_estimates
                (beatmap_id, source, status, pp_nm_100_fc, pp_nm_98_fc, pp_nm_95_fc,
                 attribute_star_rating, attribute_max_combo, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(beatmap_id) DO UPDATE SET
                source=excluded.source, status=excluded.status,
                pp_nm_100_fc=excluded.pp_nm_100_fc, pp_nm_98_fc=excluded.pp_nm_98_fc,
                pp_nm_95_fc=excluded.pp_nm_95_fc,
                attribute_star_rating=excluded.attribute_star_rating,
                attribute_max_combo=excluded.attribute_max_combo,
                error=excluded.error, fetched_at=CURRENT_TIMESTAMP
            """,
            (
                beatmap_id,
                source,
                status,
                pp_nm_100_fc,
                pp_nm_98_fc,
                pp_nm_95_fc,
                attribute_star_rating,
                attribute_max_combo,
                error,
            ),
        )

    def clear_attribute_pp_estimates(self) -> int:
        cursor = self._connection.execute("DELETE FROM beatmap_pp_estimates")
        self._connection.commit()
        return cursor.rowcount

    def pending_full_pp_rows(self, *, limit: int | None = None) -> list[sqlite3.Row]:
        return list(
            self._connection.execute(
                """
                SELECT d.id, d.mode, d.status
                FROM difficulties d
                LEFT JOIN beatmap_pp_full p ON p.beatmap_id=d.id
                WHERE d.mode='osu' AND lower(d.status)='ranked'
                  AND EXISTS (
                      SELECT 1 FROM difficulty_user_tags ut
                      WHERE ut.difficulty_id=d.id
                  )
                  AND COALESCE(p.status, '') <> 'ok'
                ORDER BY d.id
                LIMIT ?
                """,
                (-1 if limit is None else limit,),
            )
        )

    def upsert_full_pp(
        self,
        beatmap_id: int,
        *,
        osu_file_path: str,
        status: str,
        scenarios_json: str | None = None,
        components_json: str | None = None,
        error: str | None = None,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO beatmap_pp_full
                (beatmap_id, osu_file_path, status, scenarios_json, components_json, error)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(beatmap_id) DO UPDATE SET
                osu_file_path=excluded.osu_file_path, status=excluded.status,
                scenarios_json=excluded.scenarios_json, components_json=excluded.components_json,
                error=excluded.error, fetched_at=CURRENT_TIMESTAMP
            """,
            (
                beatmap_id,
                osu_file_path,
                status,
                scenarios_json,
                components_json,
                error,
            ),
        )

    def clear_user_tags(self, mapset_id: int) -> None:
        """Remove old tag rows before replacing a mapset snapshot."""
        self.tags.clear_for_mapset(mapset_id)

    def add_owner(
        self,
        difficulty_id: int,
        owner: Owner,
        depth: int,
        discovered_from: int,
        enqueue: bool = True,
    ) -> None:
        """Store a difficulty owner and make them traversable."""
        self.upsert_mapper(owner, depth, discovered_from, "pending" if enqueue else "ignored")
        self._connection.execute(
            "INSERT OR IGNORE INTO difficulty_owners (difficulty_id, mapper_id) VALUES (?, ?)",
            (difficulty_id, owner.id),
        )

    def add_participation(
        self,
        mapper_id: int,
        mapset_id: int,
        role: str,
        depth: int,
        discovered_from: int,
    ) -> None:
        """Store a host or guest mapset relationship."""
        self._connection.execute(
            """
            INSERT INTO mapper_mapsets (mapper_id, mapset_id, role, first_depth, discovered_from)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(mapper_id, mapset_id, role) DO UPDATE SET
                first_depth=MIN(mapper_mapsets.first_depth, excluded.first_depth)
            """,
            (mapper_id, mapset_id, role, depth, discovered_from),
        )

    def commit(self) -> None:
        """Commit the current mapset transaction."""
        self._connection.commit()

    def counts(self) -> dict[str, int]:
        """Return compact progress counts."""
        return collect_counts(self)

    def query_rows(
        self,
        statement: str,
        params: tuple[int | None, ...] = (),
    ) -> tuple[list[str], list[sqlite3.Row]]:
        """Run a read-only report query and return column names with rows."""
        cursor = self._connection.execute(statement, params)
        fieldnames = [description[0] for description in cursor.description or ()]
        return fieldnames, cursor.fetchall()
