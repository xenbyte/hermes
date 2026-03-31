-- DROP SCHEMA hermes;

CREATE SCHEMA hermes AUTHORIZATION postgres;

-- DROP SEQUENCE hermes.subscribers_id_seq;

CREATE SEQUENCE hermes.subscribers_id_seq
  INCREMENT BY 1
  MINVALUE 1
  MAXVALUE 2147483647
  START 1
  CACHE 1
  NO CYCLE;
-- DROP SEQUENCE hermes.targets_id_seq;

CREATE SEQUENCE hermes.targets_id_seq
  INCREMENT BY 1
  MINVALUE 1
  MAXVALUE 2147483647
  START 1
  CACHE 1
  NO CYCLE;-- hermes.homes definition

-- Drop table

-- DROP TABLE hermes.homes;

CREATE TABLE hermes.homes (
  url varchar NOT NULL,
  address varchar NOT NULL,
  city varchar NOT NULL,
  price int4 DEFAULT '-1'::integer NOT NULL,
  sqm int4 DEFAULT '-1'::integer NOT NULL,
  agency varchar NULL,
  date_added timestamp NOT NULL
);


-- hermes.link_codes definition

-- Drop table

-- DROP TABLE hermes.link_codes;

CREATE TABLE hermes.link_codes (
  code varchar(4) NOT NULL,
  email_address varchar NOT NULL,
  created_at timestamptz DEFAULT CURRENT_TIMESTAMP NULL,
  expires_at timestamptz NOT NULL,
  CONSTRAINT link_codes_pkey PRIMARY KEY (code)
);
CREATE INDEX link_codes_email_idx ON hermes.link_codes USING btree (email_address);
CREATE INDEX link_codes_expires_idx ON hermes.link_codes USING btree (expires_at);


-- hermes.magic_tokens definition

-- Drop table

-- DROP TABLE hermes.magic_tokens;

CREATE TABLE hermes.magic_tokens (
  token_id varchar(36) NOT NULL,
  email_address varchar NOT NULL,
  created_at timestamptz DEFAULT CURRENT_TIMESTAMP NULL,
  expires_at timestamptz NOT NULL,
  CONSTRAINT magic_tokens_pkey PRIMARY KEY (token_id)
);
CREATE INDEX magic_tokens_email_idx ON hermes.magic_tokens USING btree (email_address);
CREATE INDEX magic_tokens_expires_idx ON hermes.magic_tokens USING btree (expires_at);


-- hermes.meta definition

-- Drop table

-- DROP TABLE hermes.meta;

CREATE TABLE hermes.meta (
  id varchar NOT NULL,
  devmode_enabled bool DEFAULT false NOT NULL,
  scraper_halted bool DEFAULT false NOT NULL,
  workdir varchar NOT NULL,
  donation_link varchar NULL,
  donation_link_updated timestamp NULL
);


-- hermes.preview_cache definition

-- Drop table

-- DROP TABLE hermes.preview_cache;

CREATE TABLE hermes.preview_cache (
  url varchar NOT NULL,
  status varchar NOT NULL,
  image_url varchar NULL,
  image_bytes bytea NULL,
  content_type varchar NULL,
  fetched_at timestamptz NOT NULL,
  expires_at timestamptz NOT NULL,
  CONSTRAINT preview_cache_pkey PRIMARY KEY (url)
);
CREATE INDEX preview_cache_expires_idx ON hermes.preview_cache USING btree (expires_at);


-- hermes.subscribers definition

-- Drop table

-- DROP TABLE hermes.subscribers;

CREATE TABLE hermes.subscribers (
  id int4 GENERATED ALWAYS AS IDENTITY( INCREMENT BY 1 MINVALUE 1 MAXVALUE 2147483647 START 1 CACHE 1 NO CYCLE) NOT NULL,
  subscription_expiry timestamp DEFAULT '2099-01-01 00:00:00'::timestamp without time zone NULL,
  user_level int4 DEFAULT 0 NOT NULL,
  filter_min_price int4 DEFAULT 500 NOT NULL,
  filter_max_price int4 DEFAULT 2000 NOT NULL,
  filter_cities json DEFAULT '["amsterdam"]'::json NOT NULL,
  telegram_enabled bool DEFAULT false NOT NULL,
  telegram_id varchar NULL,
  filter_agencies json DEFAULT '["woningnet_amsterdam", "woningnet_huiswaarts", "woningnet_bovengroningen", "woningnet_eemvallei", "rebo", "woningnet_groningen", "woningnet_middenholland", "woningnet_woonkeus", "woningnet_woongaard", "krk", "alliantie", "woningnet_utrecht", "nmg", "bouwinvest", "vesteda", "vbt", "woningnet_almere", "woningnet_gooienvecht", "funda", "pararius", "woningnet_mijnwoonservice"]'::json NOT NULL,
  date_added timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
  filter_min_sqm int4 DEFAULT 0 NOT NULL,
  lang varchar DEFAULT 'en'::character varying NOT NULL,
  email_address varchar NULL,
  device_id varchar(36) NULL,
  apns_token text NULL,
  CONSTRAINT subscribers_device_id_key UNIQUE (device_id)
);
CREATE INDEX idx_subscribers_email_address ON hermes.subscribers USING btree (email_address);
CREATE INDEX idx_subscribers_device_id ON hermes.subscribers USING btree (device_id);


-- hermes.targets definition

-- Drop table

-- DROP TABLE hermes.targets;

CREATE TABLE hermes.targets (
  id int4 GENERATED ALWAYS AS IDENTITY( INCREMENT BY 1 MINVALUE 1 MAXVALUE 2147483647 START 1 CACHE 1 NO CYCLE) NOT NULL,
  agency varchar NOT NULL,
  queryurl varchar NOT NULL,
  "method" varchar NOT NULL,
  user_info jsonb NOT NULL,
  post_data jsonb DEFAULT '{}'::json NOT NULL,
  headers json DEFAULT '{}'::json NOT NULL,
  enabled bool DEFAULT false NOT NULL
);


-- hermes.error_rollups definition

-- Drop table

-- DROP TABLE hermes.error_rollups;

CREATE TABLE hermes.error_rollups (
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
CREATE INDEX error_rollups_last_seen_idx ON hermes.error_rollups USING btree (last_seen);
