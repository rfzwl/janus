-- Janus symbol registry schema (manual psql apply)

CREATE SCHEMA IF NOT EXISTS janus;

CREATE TABLE IF NOT EXISTS janus.symbol_registry (
    id BIGSERIAL PRIMARY KEY,
    canonical_symbol TEXT NOT NULL UNIQUE,
    asset_class TEXT NOT NULL DEFAULT 'EQUITY',
    currency TEXT NOT NULL DEFAULT 'USD',
    ib_conid BIGINT UNIQUE,
    webull_ticker TEXT UNIQUE,
    description TEXT
);
