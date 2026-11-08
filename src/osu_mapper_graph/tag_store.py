"""SQLite persistence for user-tag hydration."""

from __future__ import annotations

import json
import sqlite3
from typing import final

from .models import UserTag


@final
class TagStore:
    """Persist and query user tags using an owned SQLite connection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def untagged_difficulty_ids(self) -> list[int]:
        """Return ranked osu! difficulties in mapsets whose tags are pending."""
        rows = self._connection.execute(
            """
            SELECT d.id FROM difficulties d JOIN mapsets s ON s.id=d.mapset_id
            WHERE d.mode='osu' AND d.status='ranked' AND s.user_tags_fetched=0
            ORDER BY d.id
            """
        )
        return [int(row[0]) for row in rows]

    def clear_for_mapsets(self, mapset_ids: set[int]) -> None:
        """Clear tag rows for a batch of mapsets before replacement."""
        if not mapset_ids:
            return
        encoded_ids = json.dumps(sorted(mapset_ids), separators=(",", ":"))
        self._connection.execute(
            """
            DELETE FROM difficulty_user_tags WHERE difficulty_id IN (
                SELECT id FROM difficulties
                WHERE mapset_id IN (SELECT value FROM json_each(?))
            )
            """,
            (encoded_ids,),
        )

    def clear_for_mapset(self, mapset_id: int) -> None:
        """Remove old tag rows before replacing one mapset snapshot."""
        self._connection.execute(
            """
            DELETE FROM difficulty_user_tags
            WHERE difficulty_id IN (SELECT id FROM difficulties WHERE mapset_id=?)
            """,
            (mapset_id,),
        )

    def add(self, difficulty_id: int, tag: UserTag | None, tag_id: int, vote_count: int) -> None:
        """Store a displayed user tag, retaining its ID if metadata is absent."""
        self._connection.execute(
            """
            INSERT INTO difficulty_user_tags
                (difficulty_id, tag_id, tag_name, vote_count, ruleset_id, description)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(difficulty_id, tag_id) DO UPDATE SET
                tag_name=excluded.tag_name, vote_count=excluded.vote_count,
                ruleset_id=excluded.ruleset_id, description=excluded.description
            """,
            (
                difficulty_id,
                tag_id,
                tag.name if tag else None,
                vote_count,
                tag.ruleset_id if tag else None,
                tag.description if tag else None,
            ),
        )

    def mark_fetched(self, mapset_ids: set[int]) -> None:
        """Mark mapsets whose user tags were hydrated successfully."""
        if not mapset_ids:
            return
        encoded_ids = json.dumps(sorted(mapset_ids), separators=(",", ":"))
        self._connection.execute(
            "UPDATE mapsets SET user_tags_fetched=1 WHERE id IN (SELECT value FROM json_each(?))",
            (encoded_ids,),
        )
