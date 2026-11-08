"""SQLite schema for the crawler database."""

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS mappers (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL,
    first_depth INTEGER NOT NULL,
    discovered_from INTEGER,
    state TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    discovered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    processed_at TEXT,
    last_error TEXT
);
CREATE TABLE IF NOT EXISTS mapsets (
    id INTEGER PRIMARY KEY,
    status TEXT NOT NULL,
    artist TEXT NOT NULL,
    artist_unicode TEXT NOT NULL,
    title TEXT NOT NULL,
    title_unicode TEXT NOT NULL,
    creator TEXT NOT NULL,
    creator_id INTEGER,
    source TEXT NOT NULL,
    tags TEXT NOT NULL,
    ranked_date TEXT,
    submitted_date TEXT,
    last_updated TEXT,
    user_tags_fetched INTEGER NOT NULL DEFAULT 0,
    fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS difficulties (
    id INTEGER PRIMARY KEY,
    mapset_id INTEGER NOT NULL REFERENCES mapsets(id),
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    version TEXT NOT NULL,
    star_rating REAL,
    ar REAL,
    cs REAL,
    od REAL,
    hp REAL,
    bpm REAL,
    total_length INTEGER,
    hit_length INTEGER,
    count_circles INTEGER,
    count_sliders INTEGER,
    count_spinners INTEGER
);
CREATE TABLE IF NOT EXISTS difficulty_owners (
    difficulty_id INTEGER NOT NULL REFERENCES difficulties(id),
    mapper_id INTEGER NOT NULL REFERENCES mappers(id),
    PRIMARY KEY (difficulty_id, mapper_id)
);
CREATE TABLE IF NOT EXISTS difficulty_user_tags (
    difficulty_id INTEGER NOT NULL REFERENCES difficulties(id),
    tag_id INTEGER NOT NULL,
    tag_name TEXT,
    vote_count INTEGER NOT NULL,
    ruleset_id INTEGER,
    description TEXT,
    PRIMARY KEY (difficulty_id, tag_id)
);
CREATE TABLE IF NOT EXISTS mapper_mapsets (
    mapper_id INTEGER NOT NULL REFERENCES mappers(id),
    mapset_id INTEGER NOT NULL REFERENCES mapsets(id),
    role TEXT NOT NULL CHECK (role IN ('host', 'guest')),
    first_depth INTEGER NOT NULL,
    discovered_from INTEGER,
    PRIMARY KEY (mapper_id, mapset_id, role)
);
CREATE TABLE IF NOT EXISTS beatmap_pp_estimates (
    beatmap_id INTEGER PRIMARY KEY REFERENCES difficulties(id),
    source TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ok', 'failed')),
    pp_nm_100_fc REAL,
    pp_nm_98_fc REAL,
    pp_nm_95_fc REAL,
    attribute_star_rating REAL,
    attribute_max_combo INTEGER,
    error TEXT,
    fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS beatmap_pp_full (
    beatmap_id INTEGER PRIMARY KEY REFERENCES difficulties(id),
    osu_file_path TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ok', 'failed')),
    scenarios_json TEXT,
    components_json TEXT,
    error TEXT,
    fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""
