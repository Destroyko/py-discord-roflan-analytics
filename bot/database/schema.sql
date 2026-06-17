CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    author_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    guild_id TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    reaction_count INTEGER NOT NULL DEFAULT 0,
    last_scanned_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_guild_created
    ON messages (guild_id, created_at);

CREATE INDEX IF NOT EXISTS idx_messages_author
    ON messages (author_id);

-- Block B: scanned rows land here first; committed into `messages` in one
-- transaction only after a fully successful run (see docs/V2_PLAN.md §4).
CREATE TABLE IF NOT EXISTS messages_staging (
    message_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    author_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    guild_id TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    reaction_count INTEGER NOT NULL DEFAULT 0,
    last_scanned_at TIMESTAMP NOT NULL,
    PRIMARY KEY (message_id, run_id)
);

CREATE INDEX IF NOT EXISTS idx_staging_run
    ON messages_staging (run_id);

-- Last Rofler role holders per guild (strip by known IDs; no Members Intent).
CREATE TABLE IF NOT EXISTS rofler_role_holders (
    guild_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_rofler_holders_guild
    ON rofler_role_holders (guild_id);
