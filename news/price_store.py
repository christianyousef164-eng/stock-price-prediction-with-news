from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable

import pandas as pd
import yfinance as yf
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import HistoricalPrice, IntradayPrice, Stock

logger = logging.getLogger(__name__)
BENCHMARK_SYMBOLS = ["SPY", "^VIX"]
INTRADAY_INTERVAL = "15m"

def _artifact_candidates(filename: str) -> list[Path]:
    base_dir = Path(getattr(settings, "BASE_DIR", Path.cwd()))
    return [
        base_dir / "model_artifacts" / filename,
        base_dir / filename,
        Path("/mnt/data/model_artifacts") / filename,
        Path("/mnt/data") / filename,
    ]


def _find_artifact(filename: str) -> Path:
    for path in _artifact_candidates(filename):
        if path.exists():
            return path
    raise FileNotFoundError(f"Could not find artifact: {filename}")


def trained_symbols() -> list[str]:
    with open(_find_artifact("ticker_to_company_id.json"), "r", encoding="utf-8") as f:
        raw = json.load(f)
    return sorted(str(k).upper() for k in raw.keys())


def sync_universe_symbols() -> list[str]:
    symbols = trained_symbols()
    for bench in BENCHMARK_SYMBOLS:
        if bench not in symbols:
            symbols.append(bench)
    return symbols


def _normalize_daily(df: pd.DataFrame | None) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]

    df = df.reset_index()
    if "Date" not in df.columns:
        return pd.DataFrame()

    for col in ["Open", "High", "Low", "Close", "Volume"]:
        if col not in df.columns:
            df[col] = pd.NA

    df["Date"] = pd.to_datetime(df["Date"], errors="coerce", utc=True).dt.tz_convert(None).dt.normalize()
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["Date", "Open", "High", "Low", "Close"]).copy()
    df["Volume"] = df["Volume"].fillna(0).astype("int64")
    return df[["Date", "Open", "High", "Low", "Close", "Volume"]].sort_values("Date").reset_index(drop=True)


def _normalize_intraday(df: pd.DataFrame | None) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]

    df = df.reset_index()
    ts_col = None
    for candidate in ["Datetime", "Timestamp", "Date"]:
        if candidate in df.columns:
            ts_col = candidate
            break
    if ts_col is None:
        return pd.DataFrame()

    df = df.rename(columns={ts_col: "Timestamp"})
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        if col not in df.columns:
            df[col] = pd.NA

    df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors="coerce", utc=True)
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["Timestamp", "Open", "High", "Low", "Close"]).copy()
    df["Volume"] = df["Volume"].fillna(0).astype("int64")
    return df[["Timestamp", "Open", "High", "Low", "Close", "Volume"]].sort_values("Timestamp").reset_index(drop=True)


def download_history(symbol: str, years: int = 5) -> pd.DataFrame:
    symbol = symbol.upper().strip()
    end_dt = pd.Timestamp.utcnow().tz_localize(None).normalize()
    start_dt = end_dt - pd.DateOffset(years=years)

    try:
        df = yf.download(
            symbol,
            start=start_dt.strftime("%Y-%m-%d"),
            end=(end_dt + pd.Timedelta(days=5)).strftime("%Y-%m-%d"),
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=False,
        )
        df = _normalize_daily(df)
        if not df.empty:
            return df
    except Exception as exc:
        logger.warning("yf.download daily failed for %s: %s", symbol, exc)

    try:
        df = yf.Ticker(symbol).history(
            start=start_dt.strftime("%Y-%m-%d"),
            end=(end_dt + pd.Timedelta(days=5)).strftime("%Y-%m-%d"),
            interval="1d",
            auto_adjust=False,
            actions=False,
        )
        df = _normalize_daily(df)
        if not df.empty:
            return df
    except Exception as exc:
        logger.warning("Ticker.history daily failed for %s: %s", symbol, exc)

    return pd.DataFrame()


def download_intraday(symbol: str, days: int = 30, interval: str = INTRADAY_INTERVAL) -> pd.DataFrame:
    symbol = symbol.upper().strip()
    period = f"{max(int(days), 1)}d"

    try:
        df = yf.download(
            symbol,
            period=period,
            interval=interval,
            auto_adjust=False,
            progress=False,
            threads=False,
        )
        df = _normalize_intraday(df)
        if not df.empty:
            return df
    except Exception as exc:
        logger.warning("yf.download intraday failed for %s: %s", symbol, exc)

    try:
        df = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=False, actions=False)
        df = _normalize_intraday(df)
        if not df.empty:
            return df
    except Exception as exc:
        logger.warning("Ticker.history intraday failed for %s: %s", symbol, exc)

    return pd.DataFrame()


@transaction.atomic
def upsert_price_history(symbol: str, years: int = 5) -> int:
    symbol = symbol.upper().strip()
    df = download_history(symbol, years=years)
    if df.empty:
        return 0

    stock = Stock.objects.filter(symbol=symbol).first()
    dates = [ts.date() for ts in df["Date"].tolist()]
    existing = {
        obj.date: obj
        for obj in HistoricalPrice.objects.filter(symbol=symbol, date__in=dates)
    }

    to_create = []
    to_update = []

    for row in df.itertuples(index=False):
        day = row.Date.date()
        if day in existing:
            obj = existing[day]
            obj.stock = stock
            obj.open = float(row.Open)
            obj.high = float(row.High)
            obj.low = float(row.Low)
            obj.close = float(row.Close)
            obj.volume = int(row.Volume)
            obj.source = "yfinance"
            to_update.append(obj)
        else:
            to_create.append(
                HistoricalPrice(
                    symbol=symbol,
                    stock=stock,
                    date=day,
                    open=float(row.Open),
                    high=float(row.High),
                    low=float(row.Low),
                    close=float(row.Close),
                    volume=int(row.Volume),
                    source="yfinance",
                )
            )

    if to_create:
        HistoricalPrice.objects.bulk_create(to_create, batch_size=1000)

    if to_update:
        HistoricalPrice.objects.bulk_update(
            to_update,
            ["stock", "open", "high", "low", "close", "volume", "source", "updated_at"],
            batch_size=1000,
        )

    return len(to_create) + len(to_update)


@transaction.atomic
def upsert_intraday_history(symbol: str, days: int = 30, interval: str = INTRADAY_INTERVAL) -> int:
    symbol = symbol.upper().strip()
    df = download_intraday(symbol, days=days, interval=interval)
    if df.empty:
        return 0

    stock = Stock.objects.filter(symbol=symbol).first()
    timestamps = [ts.to_pydatetime() for ts in df["Timestamp"].tolist()]

    existing = {
        obj.timestamp: obj
        for obj in IntradayPrice.objects.filter(symbol=symbol, timestamp__in=timestamps)
    }

    to_create = []
    to_update = []

    for row in df.itertuples(index=False):
        ts = row.Timestamp.to_pydatetime()
        if ts in existing:
            obj = existing[ts]
            obj.stock = stock
            obj.open = float(row.Open)
            obj.high = float(row.High)
            obj.low = float(row.Low)
            obj.close = float(row.Close)
            obj.volume = int(row.Volume)
            obj.source = "yfinance"
            to_update.append(obj)
        else:
            to_create.append(
                IntradayPrice(
                    symbol=symbol,
                    stock=stock,
                    timestamp=ts,
                    open=float(row.Open),
                    high=float(row.High),
                    low=float(row.Low),
                    close=float(row.Close),
                    volume=int(row.Volume),
                    source="yfinance",
                )
            )

    if to_create:
        IntradayPrice.objects.bulk_create(to_create, batch_size=2000)

    if to_update:
        IntradayPrice.objects.bulk_update(
            to_update,
            ["stock", "open", "high", "low", "close", "volume", "source", "updated_at"],
            batch_size=2000,
        )

    return len(to_create) + len(to_update)


def sync_many(symbols: Iterable[str], years: int = 5) -> dict[str, int]:
    results: dict[str, int] = {}
    for symbol in symbols:
        try:
            results[symbol] = upsert_price_history(symbol, years=years)
        except Exception as exc:
            logger.exception("Failed syncing %s", symbol)
            results[symbol] = -1
            print(f"{symbol}: FAILED -> {exc}")
    return results


def sync_many_intraday(symbols: Iterable[str], days: int = 30, interval: str = "60m") -> dict[str, int]:
    results: dict[str, int] = {}
    for symbol in symbols:
        try:
            results[symbol] = upsert_intraday_history(symbol, days=days, interval=interval)
        except Exception as exc:
            logger.exception("Failed syncing intraday %s", symbol)
            results[symbol] = -1
            print(f"{symbol}: FAILED -> {exc}")
    return results
