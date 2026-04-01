-- On-demand listing analysis schema migration
-- Apply via:
--   kubectl exec -i -n hermes statefulset/postgres -- psql -U hermes -d hermes < misc/20260401_on_demand_schema.sql
--   docker exec -i hermes-database psql -U hermes -d hermes < misc/20260401_on_demand_schema.sql

-- Add url_hash to homes for O(1) callback lookup (callback_data = "analyse:<32 hex chars>")
ALTER TABLE hermes.homes ADD COLUMN IF NOT EXISTS url_hash TEXT;
UPDATE hermes.homes
SET url_hash = LEFT(encode(sha256(url::bytea), 'hex'), 32)
WHERE url_hash IS NULL;
CREATE INDEX IF NOT EXISTS idx_homes_url_hash ON hermes.homes (url_hash);

-- Cache table for on-demand analyses (one row per listing+profile pair)
CREATE TABLE IF NOT EXISTS hermes.listing_analysis (
    url_hash     TEXT        NOT NULL,
    profile_id   INTEGER     NOT NULL,
    url          TEXT        NOT NULL,
    listing_json JSONB,
    verdict_json JSONB,
    reply_text   TEXT,
    letter_nl    TEXT,
    letter_en    TEXT,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (url_hash, profile_id)
);
CREATE INDEX IF NOT EXISTS idx_listing_analysis_profile ON hermes.listing_analysis (profile_id);
