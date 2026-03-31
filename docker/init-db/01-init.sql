CREATE SCHEMA IF NOT EXISTS hermes;

CREATE TABLE IF NOT EXISTS hermes.homes (
  url varchar NOT NULL,
  address varchar NOT NULL,
  city varchar NOT NULL,
  price int4 DEFAULT '-1'::integer NOT NULL,
  sqm int4 DEFAULT '-1'::integer NOT NULL,
  agency varchar NULL,
  date_added timestamp NOT NULL
);

CREATE TABLE IF NOT EXISTS hermes.link_codes (
  code varchar(4) NOT NULL,
  email_address varchar NOT NULL,
  created_at timestamptz DEFAULT CURRENT_TIMESTAMP NULL,
  expires_at timestamptz NOT NULL,
  CONSTRAINT link_codes_pkey PRIMARY KEY (code)
);
CREATE INDEX IF NOT EXISTS link_codes_email_idx ON hermes.link_codes USING btree (email_address);
CREATE INDEX IF NOT EXISTS link_codes_expires_idx ON hermes.link_codes USING btree (expires_at);

CREATE TABLE IF NOT EXISTS hermes.magic_tokens (
  token_id varchar(36) NOT NULL,
  email_address varchar NOT NULL,
  created_at timestamptz DEFAULT CURRENT_TIMESTAMP NULL,
  expires_at timestamptz NOT NULL,
  CONSTRAINT magic_tokens_pkey PRIMARY KEY (token_id)
);
CREATE INDEX IF NOT EXISTS magic_tokens_email_idx ON hermes.magic_tokens USING btree (email_address);
CREATE INDEX IF NOT EXISTS magic_tokens_expires_idx ON hermes.magic_tokens USING btree (expires_at);

CREATE TABLE IF NOT EXISTS hermes.meta (
  id varchar NOT NULL,
  devmode_enabled bool DEFAULT false NOT NULL,
  scraper_halted bool DEFAULT false NOT NULL,
  workdir varchar NOT NULL,
  donation_link varchar NULL,
  donation_link_updated timestamp NULL
);

INSERT INTO hermes.meta (id, devmode_enabled, scraper_halted, workdir)
SELECT 'default', false, false, '/data'
WHERE NOT EXISTS (SELECT 1 FROM hermes.meta WHERE id = 'default');

CREATE TABLE IF NOT EXISTS hermes.preview_cache (
  url varchar NOT NULL,
  status varchar NOT NULL,
  image_url varchar NULL,
  image_bytes bytea NULL,
  content_type varchar NULL,
  fetched_at timestamptz NOT NULL,
  expires_at timestamptz NOT NULL,
  CONSTRAINT preview_cache_pkey PRIMARY KEY (url)
);
CREATE INDEX IF NOT EXISTS preview_cache_expires_idx ON hermes.preview_cache USING btree (expires_at);

CREATE TABLE IF NOT EXISTS hermes.subscribers (
  id int4 GENERATED ALWAYS AS IDENTITY( INCREMENT BY 1 MINVALUE 1 MAXVALUE 2147483647 START 1 CACHE 1 NO CYCLE) NOT NULL,
  subscription_expiry timestamp DEFAULT '2099-01-01 00:00:00'::timestamp without time zone NULL,
  user_level int4 DEFAULT 0 NOT NULL,
  filter_min_price int4 DEFAULT 500 NOT NULL,
  filter_max_price int4 DEFAULT 2000 NOT NULL,
  filter_cities json DEFAULT '["amsterdam"]'::json NOT NULL,
  telegram_enabled bool DEFAULT false NOT NULL,
  telegram_id varchar NULL,
  filter_agencies json DEFAULT '[]'::json NOT NULL,
  date_added timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
  filter_min_sqm int4 DEFAULT 0 NOT NULL,
  lang varchar DEFAULT 'en'::character varying NOT NULL,
  email_address varchar NULL,
  device_id varchar(36) NULL,
  apns_token text NULL,
  CONSTRAINT subscribers_device_id_key UNIQUE (device_id)
);
CREATE INDEX IF NOT EXISTS idx_subscribers_email_address ON hermes.subscribers USING btree (email_address);
CREATE INDEX IF NOT EXISTS idx_subscribers_device_id ON hermes.subscribers USING btree (device_id);

CREATE TABLE IF NOT EXISTS hermes.targets (
  id int4 GENERATED ALWAYS AS IDENTITY( INCREMENT BY 1 MINVALUE 1 MAXVALUE 2147483647 START 1 CACHE 1 NO CYCLE) NOT NULL,
  agency varchar NOT NULL,
  queryurl varchar NOT NULL,
  "method" varchar NOT NULL,
  user_info jsonb NOT NULL,
  post_data jsonb DEFAULT '{}'::json NOT NULL,
  headers json DEFAULT '{}'::json NOT NULL,
  enabled bool DEFAULT false NOT NULL
);

CREATE TABLE IF NOT EXISTS hermes.error_rollups (
  day date NOT NULL,
  fingerprint varchar(64) NOT NULL,
  component varchar NOT NULL,
  agency varchar NOT NULL,
  target_id int4 DEFAULT 0 NOT NULL,
  error_class varchar NOT NULL,
  message text NOT NULL,
  sample text NULL,
  context jsonb DEFAULT '{}'::jsonb NOT NULL,
  count int4 DEFAULT 1 NOT NULL,
  first_seen timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
  last_seen timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
  CONSTRAINT error_rollups_pkey PRIMARY KEY (day, fingerprint)
);
CREATE INDEX IF NOT EXISTS error_rollups_last_seen_idx ON hermes.error_rollups USING btree (last_seen);

-- Enrichment tables
CREATE TABLE IF NOT EXISTS hermes.user_profiles (
    id                   SERIAL PRIMARY KEY,
    telegram_id          TEXT NOT NULL UNIQUE,
    full_name            TEXT NOT NULL,
    age                  INTEGER,
    nationality          TEXT,
    languages            TEXT[],
    bsn_held             BOOLEAN DEFAULT false,
    gemeente             TEXT,
    employer             TEXT,
    contract_type        TEXT,
    gross_monthly_income INTEGER,
    employment_duration  TEXT,
    work_address         TEXT,
    max_rent             INTEGER NOT NULL,
    target_cities        TEXT[] NOT NULL,
    furnishing_pref      TEXT DEFAULT 'either',
    occupants            TEXT DEFAULT 'single',
    pets                 TEXT,
    owned_items          TEXT,
    move_in_date         TEXT,
    extra_notes          TEXT,
    created_at           TIMESTAMPTZ DEFAULT now(),
    updated_at           TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS hermes.enrichment_queue (
    id           TEXT NOT NULL,
    profile_id   INTEGER NOT NULL REFERENCES hermes.user_profiles(id),
    url          TEXT NOT NULL,
    address      TEXT,
    city         TEXT,
    price        INTEGER,
    agency       TEXT,
    sqm          INTEGER DEFAULT -1,
    enqueued_at  TIMESTAMPTZ DEFAULT now(),
    status       TEXT DEFAULT 'pending',
    retry_count  INTEGER DEFAULT 0,
    page_text    TEXT,
    fetch_method TEXT,
    PRIMARY KEY (id, profile_id)
);
CREATE INDEX IF NOT EXISTS idx_enrichment_queue_drain ON hermes.enrichment_queue (status, enqueued_at);

CREATE TABLE IF NOT EXISTS hermes.enrichment_results (
    id               TEXT NOT NULL,
    profile_id       INTEGER NOT NULL REFERENCES hermes.user_profiles(id),
    url              TEXT NOT NULL,
    score            INTEGER,
    compatible       BOOLEAN,
    confidence       TEXT,
    rejection_reason TEXT,
    listing_json     JSONB,
    trade_offs       TEXT[],
    recommendation   TEXT,
    income_check     JSONB,
    expat_flags      TEXT[],
    letter_nl        TEXT,
    letter_en        TEXT,
    analyzed_at      TIMESTAMPTZ DEFAULT now(),
    model_used       TEXT,
    PRIMARY KEY (id, profile_id)
);
CREATE INDEX IF NOT EXISTS idx_enrichment_results_profile_score ON hermes.enrichment_results (profile_id, score);

CREATE TABLE IF NOT EXISTS hermes.llm_usage (
    id             SERIAL PRIMARY KEY,
    batch_id       TEXT,
    model          TEXT,
    input_tokens   INTEGER,
    output_tokens  INTEGER,
    estimated_cost NUMERIC(10,6),
    called_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_llm_usage_called_at ON hermes.llm_usage (called_at);
