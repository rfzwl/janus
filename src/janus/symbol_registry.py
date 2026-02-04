import logging
from threading import RLock
from dataclasses import dataclass
from typing import Dict, Optional, Any

import psycopg


@dataclass
class SymbolRecord:
    canonical_symbol: str
    asset_class: str
    currency: str
    ib_conid: Optional[int]
    webull_ticker: Optional[str]
    description: Optional[str]


class SymbolRegistry:
    def __init__(self, settings: Dict[str, Any], logger: Optional[logging.Logger] = None) -> None:
        self.logger = logger or logging.getLogger("SymbolRegistry")
        self._settings = settings
        self._conn = self._connect(settings)
        self._lock = RLock()
        self._cache_by_canonical: Dict[str, SymbolRecord] = {}
        self._cache_by_webull: Dict[str, SymbolRecord] = {}
        self._cache_by_ib_conid: Dict[int, SymbolRecord] = {}
        self._load_cache()

    @staticmethod
    def normalize(symbol: str) -> str:
        return symbol.strip().upper()

    def _connect(self, settings: Dict[str, Any]):
        dbname = settings.get("name") or settings.get("database") or "postgres"
        params = {
            "dbname": dbname,
            "host": settings.get("host", "localhost"),
            "port": settings.get("port", 5432),
            "user": settings.get("user"),
            "password": settings.get("password"),
        }
        # Drop empty values so psycopg can use defaults
        params = {k: v for k, v in params.items() if v not in (None, "")}
        conn = psycopg.connect(**params)
        conn.autocommit = True
        return conn

    def _load_cache(self) -> None:
        sql = (
            "SELECT canonical_symbol, asset_class, currency, ib_conid, webull_ticker, description "
            "FROM janus.symbol_registry"
        )
        with self._conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
        for row in rows:
            record = SymbolRecord(
                canonical_symbol=self.normalize(row[0]),
                asset_class=row[1],
                currency=row[2],
                ib_conid=row[3],
                webull_ticker=self.normalize(row[4]) if row[4] else None,
                description=row[5],
            )
            self._cache_by_canonical[record.canonical_symbol] = record
            if record.webull_ticker:
                self._cache_by_webull[record.webull_ticker] = record
            if record.ib_conid is not None:
                self._cache_by_ib_conid[record.ib_conid] = record

    def get_by_canonical(self, symbol: str) -> Optional[SymbolRecord]:
        canonical = self.normalize(symbol)
        return self._cache_by_canonical.get(canonical)

    def get_by_ib_conid(self, conid: int) -> Optional[SymbolRecord]:
        return self._cache_by_ib_conid.get(conid)

    def list_records(self) -> list[SymbolRecord]:
        with self._lock:
            return list(self._cache_by_canonical.values())

    def ensure_webull_symbol(
        self,
        ticker: str,
        asset_class: Optional[str] = None,
        currency: Optional[str] = None,
        description: Optional[str] = None,
    ) -> SymbolRecord:
        with self._lock:
            canonical = self.normalize(ticker)
            record = self._cache_by_canonical.get(canonical)

            if record is None:
                record = self._insert_webull_symbol(
                    canonical,
                    asset_class or "EQUITY",
                    currency or "USD",
                    description,
                )
                return record

            if record.webull_ticker and record.webull_ticker != canonical:
                self.logger.warning(
                    "Webull ticker mismatch for %s: registry has %s", canonical, record.webull_ticker
                )
                return record

            # Fill missing webull_ticker if absent
            if not record.webull_ticker:
                self._update_webull_ticker(canonical, canonical)
                record.webull_ticker = canonical
                self._cache_by_webull[canonical] = record

            # Fill description only if empty (first value wins)
            if description and not record.description:
                self._update_description(canonical, description)
                record.description = description

            return record

    def ensure_ib_symbol(
        self,
        symbol: str,
        conid: int,
        asset_class: Optional[str] = None,
        currency: Optional[str] = None,
        description: Optional[str] = None,
    ) -> SymbolRecord:
        with self._lock:
            canonical = self.normalize(symbol)

            existing_by_conid = self._cache_by_ib_conid.get(conid)
            if existing_by_conid and existing_by_conid.canonical_symbol != canonical:
                self.logger.warning(
                    "IB conId %s already mapped to %s; skip %s",
                    conid,
                    existing_by_conid.canonical_symbol,
                    canonical,
                )
                return existing_by_conid

            record = self._cache_by_canonical.get(canonical)
            if record is None:
                record = self._insert_ib_symbol(
                    canonical,
                    asset_class or "EQUITY",
                    currency or "USD",
                    conid,
                    description,
                )
                return record

            if record.ib_conid and record.ib_conid != conid:
                self.logger.warning(
                    "IB conId mismatch for %s: registry has %s",
                    canonical,
                    record.ib_conid,
                )
                return record

            if not record.ib_conid:
                self._update_ib_conid(canonical, conid)
                record.ib_conid = conid
                self._cache_by_ib_conid[conid] = record

            if description and not record.description:
                self._update_description(canonical, description)
                record.description = description

            return record

    def _insert_webull_symbol(
        self,
        canonical: str,
        asset_class: str,
        currency: str,
        description: Optional[str],
    ) -> SymbolRecord:
        sql = (
            "INSERT INTO janus.symbol_registry "
            "(canonical_symbol, asset_class, currency, webull_ticker, description) "
            "VALUES (%s, %s, %s, %s, %s)"
        )
        with self._conn.cursor() as cur:
            cur.execute(sql, (canonical, asset_class, currency, canonical, description))
        record = SymbolRecord(
            canonical_symbol=canonical,
            asset_class=asset_class,
            currency=currency,
            ib_conid=None,
            webull_ticker=canonical,
            description=description,
        )
        self._cache_by_canonical[canonical] = record
        self._cache_by_webull[canonical] = record
        return record

    def _insert_ib_symbol(
        self,
        canonical: str,
        asset_class: str,
        currency: str,
        conid: int,
        description: Optional[str],
    ) -> SymbolRecord:
        sql = (
            "INSERT INTO janus.symbol_registry "
            "(canonical_symbol, asset_class, currency, ib_conid, description) "
            "VALUES (%s, %s, %s, %s, %s)"
        )
        with self._conn.cursor() as cur:
            cur.execute(sql, (canonical, asset_class, currency, conid, description))
        record = SymbolRecord(
            canonical_symbol=canonical,
            asset_class=asset_class,
            currency=currency,
            ib_conid=conid,
            webull_ticker=None,
            description=description,
        )
        self._cache_by_canonical[canonical] = record
        self._cache_by_ib_conid[conid] = record
        return record

    def _update_webull_ticker(self, canonical: str, ticker: str) -> None:
        sql = "UPDATE janus.symbol_registry SET webull_ticker = %s WHERE canonical_symbol = %s"
        with self._conn.cursor() as cur:
            cur.execute(sql, (ticker, canonical))

    def _update_ib_conid(self, canonical: str, conid: int) -> None:
        sql = "UPDATE janus.symbol_registry SET ib_conid = %s WHERE canonical_symbol = %s"
        with self._conn.cursor() as cur:
            cur.execute(sql, (conid, canonical))

    def _update_description(self, canonical: str, description: str) -> None:
        sql = "UPDATE janus.symbol_registry SET description = %s WHERE canonical_symbol = %s"
        with self._conn.cursor() as cur:
            cur.execute(sql, (description, canonical))
