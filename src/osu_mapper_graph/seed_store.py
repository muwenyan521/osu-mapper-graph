"""Mapper seed queue operations over SQLite."""

from __future__ import annotations

import json
import sqlite3

from .models import User


def prepare_batch(connection: sqlite3.Connection, users: list[User]) -> None:
    """Force users to depth zero and queue them for expansion."""
    for user in users:
        connection.execute(
            """
            INSERT INTO mappers (id, username, first_depth, discovered_from, state)
            VALUES (?, ?, 0, NULL, 'pending')
            ON CONFLICT(id) DO UPDATE SET
                username=excluded.username, first_depth=0,
                discovered_from=NULL, state='pending', last_error=NULL
            """,
            (user.id, user.username),
        )
    connection.commit()


def promote_mapset_participants(connection: sqlite3.Connection, mapset_ids: list[int]) -> list[int]:
    """Promote all known owners of mapsets to depth-zero seed jobs."""
    if not mapset_ids:
        return []
    encoded_ids = json.dumps(sorted(mapset_ids), separators=(",", ":"))
    rows = connection.execute(
        "SELECT DISTINCT mapper_id FROM mapper_mapsets WHERE mapset_id IN (SELECT value FROM json_each(?))",
        (encoded_ids,),
    )
    mapper_ids = [int(row[0]) for row in rows]
    if mapper_ids:
        encoded_mapper_ids = json.dumps(sorted(mapper_ids), separators=(",", ":"))
        connection.execute(
            """
            UPDATE mappers SET first_depth=0, discovered_from=NULL,
                state='pending', last_error=NULL
            WHERE id IN (SELECT value FROM json_each(?))
            """,
            (encoded_mapper_ids,),
        )
    connection.commit()
    return mapper_ids


def refresh_user_tags(connection: sqlite3.Connection) -> None:
    """Queue existing mappers to hydrate user tags for old mapsets."""
    connection.execute("UPDATE mapsets SET user_tags_fetched=0")
    connection.execute("UPDATE mappers SET state='pending', processed_at=NULL WHERE state='done'")
    connection.commit()
