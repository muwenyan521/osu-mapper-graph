"""Export the normalized graph into a flat analysis table."""

from __future__ import annotations

import csv
from pathlib import Path

from .database import Database

EXPORT_QUERY = """
SELECT m.id AS mapper_id, m.username AS mapper_name, mm.role,
       s.id AS mapset_id, s.artist, s.title, s.creator, s.creator_id,
       d.id AS beatmap_id, d.version, d.status, d.star_rating, d.ar, d.cs,
       d.od, d.hp, d.bpm, d.total_length, d.hit_length,
       p.status AS pp_full_status,
       p.osu_file_path AS pp_full_osu_file_path,
       p.scenarios_json AS pp_full_scenarios_json,
       p.components_json AS pp_full_components_json,
       GROUP_CONCAT(DISTINCT ut.tag_name) AS user_tag_names,
       GROUP_CONCAT(DISTINCT printf('%s:%d', COALESCE(ut.tag_name, '#' || ut.tag_id), ut.vote_count)) AS user_tag_votes,
       GROUP_CONCAT(DISTINCT owner.id) AS difficulty_owner_ids,
       GROUP_CONCAT(DISTINCT owner.username) AS difficulty_owner_names,
       mm.first_depth AS discovery_depth
FROM mapper_mapsets mm
JOIN mappers m ON m.id = mm.mapper_id
JOIN mapsets s ON s.id = mm.mapset_id
LEFT JOIN difficulties d ON d.mapset_id = s.id AND d.mode='osu' AND d.status='ranked'
LEFT JOIN difficulty_owners do ON do.difficulty_id = d.id
LEFT JOIN mappers owner ON owner.id = do.mapper_id
LEFT JOIN difficulty_user_tags ut ON ut.difficulty_id = d.id
LEFT JOIN beatmap_pp_full p ON p.beatmap_id = d.id
GROUP BY m.id, mm.mapset_id, mm.role, d.id
ORDER BY m.first_depth, m.id, s.id, d.id
"""


def export_csv(database: Database, output: Path, max_depth: int | None = None) -> int:
    """Export one row per mapper participation and osu! difficulty."""
    query = EXPORT_QUERY.replace(
        "GROUP BY m.id, mm.mapset_id, mm.role, d.id",
        "WHERE (? IS NULL OR mm.first_depth < ?)\nGROUP BY m.id, mm.mapset_id, mm.role, d.id",
    )
    fieldnames, rows = database.query_rows(query, (max_depth, max_depth))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(fieldnames)
        writer.writerows(rows)
    return len(rows)
