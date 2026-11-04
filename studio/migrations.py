"""Ordered, append-only schema migrations for studio.db.

Each entry is (version, [statements]). Never edit a released migration: add a
new one. Kept separate from the Cloud alembic tree on purpose (different
database, different lifecycle).
"""

MIGRATIONS = [
    (1, [
        """
        CREATE TABLE projects (
            id               TEXT PRIMARY KEY,
            job_id           TEXT NOT NULL UNIQUE,
            title            TEXT NOT NULL,
            source_url       TEXT,
            source_type      TEXT NOT NULL DEFAULT 'upload',
            has_source       INTEGER NOT NULL DEFAULT 0,
            rights           TEXT NOT NULL DEFAULT 'unknown',
            rights_note      TEXT,
            status           TEXT NOT NULL DEFAULT 'active',
            size_bytes       INTEGER NOT NULL DEFAULT 0,
            created_at       TEXT NOT NULL,
            updated_at       TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE clips (
            id               TEXT PRIMARY KEY,
            project_id       TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            clip_index       INTEGER NOT NULL,
            file_name        TEXT NOT NULL,
            duration         REAL,
            start_s          REAL,
            end_s            REAL,
            title            TEXT,
            hook_text        TEXT,
            caption          TEXT,
            file_sha256      TEXT,
            review_status    TEXT NOT NULL DEFAULT 'draft',
            reviewed_at      TEXT,
            metadata         TEXT,
            created_at       TEXT NOT NULL,
            updated_at       TEXT NOT NULL,
            UNIQUE (project_id, clip_index)
        )
        """,
        "CREATE INDEX ix_clips_project ON clips(project_id)",
        "CREATE INDEX ix_clips_review_status ON clips(review_status)",
        "CREATE INDEX ix_projects_created ON projects(created_at)",
        """
        CREATE TABLE settings (
            key              TEXT PRIMARY KEY,
            value            TEXT
        )
        """,
    ]),
    (2, [
        """
        CREATE TABLE channels (
            id               TEXT PRIMARY KEY,
            name             TEXT NOT NULL,
            platform         TEXT NOT NULL,
            handle           TEXT,
            language         TEXT,
            caption_preset   TEXT,
            watermark_text   TEXT,
            publisher        TEXT NOT NULL DEFAULT 'manual',
            publisher_config TEXT,
            created_at       TEXT NOT NULL,
            updated_at       TEXT NOT NULL,
            archived_at      TEXT
        )
        """,
        """
        CREATE TABLE campaigns (
            id               TEXT PRIMARY KEY,
            name             TEXT NOT NULL,
            platform_source  TEXT,
            brief_url        TEXT,
            cpm              REAL NOT NULL DEFAULT 0,
            currency         TEXT NOT NULL DEFAULT 'USD',
            min_views        INTEGER NOT NULL DEFAULT 0,
            max_payout       REAL,
            starts_at        TEXT,
            ends_at          TEXT,
            donation_pct     REAL NOT NULL DEFAULT 0,
            status           TEXT NOT NULL DEFAULT 'active',
            notes            TEXT,
            created_at       TEXT NOT NULL,
            updated_at       TEXT NOT NULL
        )
        """,
        "ALTER TABLE projects ADD COLUMN campaign_id TEXT "
        "REFERENCES campaigns(id) ON DELETE SET NULL",
        "ALTER TABLE clips ADD COLUMN hashtags TEXT",
        "CREATE INDEX ix_projects_campaign ON projects(campaign_id)",
        "CREATE INDEX ix_campaigns_status ON campaigns(status)",
    ]),
]

LATEST = MIGRATIONS[-1][0]


def apply(conn) -> int:
    """Bring a DB-API sqlite3 connection up to LATEST. Returns the final version.

    Each migration runs in its own transaction together with the version bump,
    so a crash mid-way leaves the previous version intact.
    """
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (0)")
        conn.commit()
        current = 0
    else:
        current = row[0]
    for version, statements in MIGRATIONS:
        if version <= current:
            continue
        try:
            conn.execute("BEGIN")
            for stmt in statements:
                conn.execute(stmt)
            conn.execute("UPDATE schema_version SET version = ?", (version,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        current = version
    return current
