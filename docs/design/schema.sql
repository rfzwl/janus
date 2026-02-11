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

-- IB initial download storage (phase-1: 1-minute only)
CREATE TABLE IF NOT EXISTS janus.ohlc_1min (
    symbol_id BIGINT NOT NULL,
    ts TIMESTAMPTZ NOT NULL,
    open NUMERIC(20,8) NOT NULL,
    high NUMERIC(20,8) NOT NULL,
    low NUMERIC(20,8) NOT NULL,
    close NUMERIC(20,8) NOT NULL,
    volume BIGINT NOT NULL,
    wap NUMERIC(20,8),
    source TEXT NOT NULL DEFAULT 'ib',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol_id, ts),
    CONSTRAINT fk_ohlc_1min_symbol
        FOREIGN KEY (symbol_id)
        REFERENCES janus.symbol_registry(id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ohlc_1min_ts ON janus.ohlc_1min(ts);
