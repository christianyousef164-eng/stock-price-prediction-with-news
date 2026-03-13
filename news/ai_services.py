from __future__ import annotations

import json
import logging
import os
import pickle
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import yfinance as yf
from django.conf import settings
from django.db.models import Q, QuerySet
from django.utils import timezone
from plotly.offline import plot
from plotly.subplots import make_subplots

from .models import HistoricalPrice, NewsArticle, Stock, StockPredictionSnapshot

try:
    from .models import IntradayPrice
except Exception:  # pragma: no cover
    IntradayPrice = None

logger = logging.getLogger(__name__)

VALID_CHART_RANGES = {"1D", "5D", "1M", "3M", "6M", "1Y", "3Y", "5Y"}
INTRADAY_RANGES = {"1D", "5D"}
VALID_CHART_MODES = {"candles", "line"}


@dataclass
class PredictionResult:
    symbol: str
    up_probability: float | None
    prediction: int | None
    threshold: float | None
    label: str
    quality_note: str
    feature_rows: int


# ---------------------------------------------------------------------------
# Artifact loading
# ---------------------------------------------------------------------------

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


@lru_cache(maxsize=1)
def load_model_metadata() -> dict[str, Any]:
    with open(_find_artifact("model_metadata.json"), "r", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def load_company_to_idx() -> dict[str, int]:
    with open(_find_artifact("company_to_idx.json"), "r", encoding="utf-8") as f:
        raw = json.load(f)
    return {str(k): int(v) for k, v in raw.items()}


@lru_cache(maxsize=1)
def load_ticker_to_company_id() -> dict[str, int]:
    with open(_find_artifact("ticker_to_company_id.json"), "r", encoding="utf-8") as f:
        raw = json.load(f)
    return {str(k).upper(): int(v) for k, v in raw.items()}


@lru_cache(maxsize=1)
def load_feature_scaler() -> Any:
    with open(_find_artifact("feature_scaler.pkl"), "rb") as f:
        return pickle.load(f)


@lru_cache(maxsize=1)
def load_prediction_model() -> Any:
    try:
        from tensorflow.keras.models import load_model  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("TensorFlow is required for model inference.") from exc
    return load_model(_find_artifact("final_stock_model.keras"))


# ---------------------------------------------------------------------------
# Metadata helpers
# ---------------------------------------------------------------------------

def trained_symbols() -> list[str]:
    return sorted(load_ticker_to_company_id().keys())


def raw_company_id_for_symbol(symbol: str) -> int:
    symbol = str(symbol).upper().strip()
    mapping = load_ticker_to_company_id()
    if symbol not in mapping:
        raise KeyError(f"Unsupported symbol for trained model: {symbol}")
    return mapping[symbol]


def embedding_index_for_symbol(symbol: str) -> int:
    raw_company_id = raw_company_id_for_symbol(symbol)
    return load_company_to_idx()[str(raw_company_id)]


def normalize_chart_range(value: str | None) -> str:
    value = str(value or "5D").upper().strip()
    return value if value in VALID_CHART_RANGES else "5D"


def normalize_chart_mode(value: str | None) -> str:
    value = str(value or "candles").lower().strip()
    return value if value in VALID_CHART_MODES else "candles"


# ---------------------------------------------------------------------------
# FinBERT
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _sentiment_pipeline():
    use_finbert = bool(getattr(settings, "USE_FINBERT", False))
    if not use_finbert:
        logger.warning("USE_FINBERT=0, using neutral fallback.")
        return None

    try:
        from transformers import pipeline  # type: ignore

        return pipeline(
            "sentiment-analysis",
            model="ProsusAI/finbert",
            tokenizer="ProsusAI/finbert",
            truncation=True,
            max_length=512,
        )
    except Exception as exc:
        logger.warning("FinBERT pipeline unavailable, using neutral fallback: %s", exc)
        return None


def score_finbert(texts: list[str], batch_size: int = 8) -> list[tuple[str, float, float]]:
    if not texts:
        return []

    sentiment_model = _sentiment_pipeline()
    if sentiment_model is None:
        return [("neutral", 0.0, 0.0) for _ in texts]

    out: list[tuple[str, float, float]] = []
    for i in range(0, len(texts), batch_size):
        batch = [str(x)[:3000] for x in texts[i : i + batch_size]]
        preds = sentiment_model(batch)
        for pred in preds:
            label = str(pred["label"]).lower()
            score = float(pred["score"])
            if "positive" in label:
                signed = score
            elif "negative" in label:
                signed = -score
            else:
                signed = 0.0
            out.append((label, score, signed))
    return out


# ---------------------------------------------------------------------------
# Historical / intraday price data
# ---------------------------------------------------------------------------

def _empty_price_frame(symbol: str) -> pd.DataFrame:
    return pd.DataFrame(
        columns=["Date", "Open", "High", "Low", "Close", "Adj Close", "Volume", "ticker", "company_id"]
    )


def _normalize_yf_history(df: pd.DataFrame | None, symbol: str) -> pd.DataFrame:
    if df is None or df.empty:
        return _empty_price_frame(symbol)

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]

    df = df.reset_index()
    if "Date" not in df.columns:
        return _empty_price_frame(symbol)

    for col in ["Open", "High", "Low", "Close", "Volume"]:
        if col not in df.columns:
            df[col] = np.nan

    if "Adj Close" not in df.columns:
        df["Adj Close"] = df["Close"]

    df["Date"] = pd.to_datetime(df["Date"], errors="coerce", utc=True).dt.tz_convert(None).dt.normalize()
    for col in ["Open", "High", "Low", "Close", "Adj Close", "Volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["Date", "Close"]).sort_values("Date").reset_index(drop=True)
    if df.empty:
        return _empty_price_frame(symbol)

    df["ticker"] = symbol.upper()
    try:
        df["company_id"] = raw_company_id_for_symbol(symbol)
    except Exception:
        df["company_id"] = pd.NA
    return df[["Date", "Open", "High", "Low", "Close", "Adj Close", "Volume", "ticker", "company_id"]]


def _period_to_start_date(period: str) -> pd.Timestamp:
    today = pd.Timestamp(timezone.now().date())
    mapping = {
        "18mo": today - pd.DateOffset(months=18),
        "1y": today - pd.DateOffset(years=1),
        "2y": today - pd.DateOffset(years=2),
        "3y": today - pd.DateOffset(years=3),
        "5y": today - pd.DateOffset(years=5),
    }
    return mapping.get(period, today - pd.DateOffset(months=18))


def fetch_price_history(symbol: str, period: str = "18mo") -> pd.DataFrame:
    symbol = symbol.upper().strip()
    start_date = _period_to_start_date(period).date()
    end_date = timezone.now().date()

    rows = list(
        HistoricalPrice.objects.filter(symbol=symbol, date__gte=start_date, date__lte=end_date)
        .order_by("date")
        .values("date", "open", "high", "low", "close", "volume")
    )

    if rows:
        df = pd.DataFrame.from_records(rows).rename(
            columns={
                "date": "Date",
                "open": "Open",
                "high": "High",
                "low": "Low",
                "close": "Close",
                "volume": "Volume",
            }
        )
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["Adj Close"] = df["Close"]
        df["ticker"] = symbol
        try:
            df["company_id"] = raw_company_id_for_symbol(symbol)
        except Exception:
            df["company_id"] = pd.NA
        return df.dropna(subset=["Date", "Close"]).sort_values("Date").reset_index(drop=True)

    # fallback only if DB is empty
    if not getattr(settings, "ALLOW_PRICE_NETWORK_FALLBACK", False):
        return _empty_price_frame(symbol)

    try:
        yf_df = yf.Ticker(symbol).history(period=period, interval="1d", auto_adjust=False, actions=False)
        normalized = _normalize_yf_history(yf_df, symbol)
        if not normalized.empty:
            return normalized
    except Exception as exc:
        logger.warning("Ticker.history failed for %s: %s", symbol, exc)

    try:
        yf_df = yf.download(symbol, period=period, interval="1d", auto_adjust=False, progress=False, threads=False)
        normalized = _normalize_yf_history(yf_df, symbol)
        if not normalized.empty:
            return normalized
    except Exception as exc:
        logger.warning("yf.download failed for %s: %s", symbol, exc)

    return _empty_price_frame(symbol)


def fetch_market_history(start_date: pd.Timestamp, end_date: pd.Timestamp) -> pd.DataFrame:
    start_date = pd.to_datetime(start_date).date()
    end_date = pd.to_datetime(end_date).date()

    spy_rows = list(
        HistoricalPrice.objects.filter(symbol="SPY", date__gte=start_date, date__lte=end_date)
        .order_by("date")
        .values("date", "close")
    )
    vix_rows = list(
        HistoricalPrice.objects.filter(symbol="^VIX", date__gte=start_date, date__lte=end_date)
        .order_by("date")
        .values("date", "close")
    )

    if not spy_rows or not vix_rows:
        return pd.DataFrame()

    spy = pd.DataFrame.from_records(spy_rows).rename(columns={"date": "Date", "close": "SPY_Close"})
    vix = pd.DataFrame.from_records(vix_rows).rename(columns={"date": "Date", "close": "VIX_Close"})

    spy["Date"] = pd.to_datetime(spy["Date"], errors="coerce")
    vix["Date"] = pd.to_datetime(vix["Date"], errors="coerce")
    spy["SPY_Close"] = pd.to_numeric(spy["SPY_Close"], errors="coerce")
    vix["VIX_Close"] = pd.to_numeric(vix["VIX_Close"], errors="coerce")

    market = spy.merge(vix, on="Date", how="left").sort_values("Date").reset_index(drop=True)
    market["VIX_Close"] = market["VIX_Close"].ffill().bfill()
    market["spy_ret_1d"] = market["SPY_Close"].pct_change(1)
    market["spy_ret_5d"] = market["SPY_Close"].pct_change(5)
    market["spy_ret_10d"] = market["SPY_Close"].pct_change(10)
    market["spy_vol_20"] = market["spy_ret_1d"].rolling(20).std()
    market["vix_level"] = market["VIX_Close"]
    market["high_vol_regime"] = (
        market["spy_vol_20"] > market["spy_vol_20"].rolling(60, min_periods=20).median()
    ).astype(float)

    return market.replace([np.inf, -np.inf], np.nan).ffill().bfill()


def fetch_news_history(symbol: str, max_articles: int = 20, lookback_days: int = 7) -> pd.DataFrame:
    cutoff = timezone.now() - pd.Timedelta(days=lookback_days)

    qs: QuerySet[NewsArticle] = (
        NewsArticle.objects.filter(
            stocks__symbol=symbol.upper(),
            source__iexact="Finnhub",
            published_at__gte=cutoff,
        )
        .exclude(summary__isnull=True)
        .exclude(summary__exact="")
        .order_by("-published_at")
        .distinct()[:max_articles]
    )

    records = list(
        qs.values(
            "published_at",
            "headline",
            "summary",
            "source",
            "url",
            "finbert_label",
            "finbert_confidence",
            "finbert_signed_score",
            "finbert_scored_at",
        )
    )
    if not records:
        return pd.DataFrame(
            columns=[
                "date",
                "ticker",
                "company_id",
                "content",
                "source",
                "url",
                "source_type",
                "text_length",
                "finbert_label",
                "finbert_confidence",
                "finbert_signed_score",
                "finbert_scored_at",
            ]
        )

    records = list(reversed(records))
    df = pd.DataFrame.from_records(records)
    df["date"] = pd.to_datetime(df["published_at"], errors="coerce", utc=True).dt.tz_convert(None).dt.normalize()
    df["ticker"] = symbol.upper()
    df["company_id"] = raw_company_id_for_symbol(symbol)
    df["content"] = (
        df["headline"].fillna("").astype(str).str.strip()
        + ". "
        + df["summary"].fillna("").astype(str).str.strip()
    ).str.strip(" .")
    df["source_type"] = "finnhub"
    df["text_length"] = df["content"].str.len()
    df = df[df["text_length"] > 20].copy()
    df = df.drop_duplicates(subset=["ticker", "date", "url", "content"])
    return df[
        [
            "date",
            "ticker",
            "company_id",
            "content",
            "source",
            "url",
            "source_type",
            "text_length",
            "finbert_label",
            "finbert_confidence",
            "finbert_signed_score",
            "finbert_scored_at",
        ]
    ]


def score_and_aggregate_news(news_df: pd.DataFrame) -> pd.DataFrame:
    if news_df.empty:
        return pd.DataFrame(
            columns=[
                "Date",
                "news_count",
                "sentiment_conf_mean",
                "positive_ratio_lag3",
                "negative_ratio_lag3",
                "positive_ratio_roll5_mean",
                "sentiment_mean_roll5_mean",
                "sentiment_mean_lag3",
                "news_count_roll5_mean",
                "finnhub_count",
                "sentiment_mean",
            ]
        )

    news_df = news_df.copy()

    if {"finbert_label", "finbert_confidence", "finbert_signed_score"}.issubset(news_df.columns):
        news_df["sentiment_label"] = news_df["finbert_label"].fillna("").astype(str).str.lower().replace({"": "neutral"})
        news_df["sentiment_confidence"] = pd.to_numeric(news_df["finbert_confidence"], errors="coerce").fillna(0.0)
        news_df["sentiment"] = pd.to_numeric(news_df["finbert_signed_score"], errors="coerce").fillna(0.0)
    else:
        preds = score_finbert(news_df["content"].tolist())
        news_df["sentiment_label"] = [x[0] for x in preds]
        news_df["sentiment_confidence"] = [x[1] for x in preds]
        news_df["sentiment"] = [x[2] for x in preds]

    news_df["is_positive"] = (news_df["sentiment_label"].str.contains("positive", na=False)).astype(int)
    news_df["is_negative"] = (news_df["sentiment_label"].str.contains("negative", na=False)).astype(int)
    news_df["is_finnhub"] = 1

    daily = (
        news_df.groupby("date")
        .agg(
            news_count=("content", "size"),
            sentiment_conf_mean=("sentiment_confidence", "mean"),
            sentiment_mean=("sentiment", "mean"),
            positive_ratio=("is_positive", "mean"),
            negative_ratio=("is_negative", "mean"),
            finnhub_count=("is_finnhub", "sum"),
        )
        .reset_index()
        .rename(columns={"date": "Date"})
        .sort_values("Date")
    )

    for col in ["sentiment_mean", "news_count", "positive_ratio", "negative_ratio"]:
        daily[f"{col}_lag3"] = daily[col].shift(3)
        daily[f"{col}_roll5_mean"] = daily[col].rolling(5).mean()

    return daily[
        [
            "Date",
            "news_count",
            "sentiment_conf_mean",
            "positive_ratio_lag3",
            "negative_ratio_lag3",
            "positive_ratio_roll5_mean",
            "sentiment_mean_roll5_mean",
            "sentiment_mean_lag3",
            "news_count_roll5_mean",
            "finnhub_count",
            "sentiment_mean",
        ]
    ]


# ---------------------------------------------------------------------------
# Feature engineering
# ---------------------------------------------------------------------------

def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _rsi(series: pd.Series, window: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _macd(series: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    ema12 = _ema(series, 12)
    ema26 = _ema(series, 26)
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    diff = macd - signal
    return macd, signal, diff


def _bollinger(close: pd.Series, window: int = 20) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    mid = close.rolling(window).mean()
    std = close.rolling(window).std()
    high = mid + 2 * std
    low = mid - 2 * std
    width = (high - low) / mid.replace(0, np.nan)
    pos = (close - low) / (high - low).replace(0, np.nan)
    return high, low, width, pos


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.rolling(window).mean()


def _stoch_k(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    lowest = low.rolling(window).min()
    highest = high.rolling(window).max()
    return 100 * (close - lowest) / (highest - lowest).replace(0, np.nan)


def _cci(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 20) -> pd.Series:
    typical = (high + low + close) / 3
    sma = typical.rolling(window).mean()
    mad = typical.rolling(window).apply(lambda x: np.mean(np.abs(x - np.mean(x))), raw=True)
    return (typical - sma) / (0.015 * mad.replace(0, np.nan))


def _cmf(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, window: int = 20) -> pd.Series:
    denom = (high - low).replace(0, np.nan)
    mfm = ((close - low) - (high - close)) / denom
    mfv = mfm * volume
    return mfv.rolling(window).sum() / volume.rolling(window).sum().replace(0, np.nan)


def _williams_r(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    highest = high.rolling(window).max()
    lowest = low.rolling(window).min()
    return -100 * (highest - close) / (highest - lowest).replace(0, np.nan)


def _roc(close: pd.Series, window: int = 10) -> pd.Series:
    return 100 * (close / close.shift(window) - 1)


def build_model_features(symbol: str) -> pd.DataFrame:
    metadata = load_model_metadata()
    feature_columns = metadata["feature_columns"]

    prices = fetch_price_history(symbol, period="18mo")
    if prices.empty:
        raise ValueError(f"No price history available for {symbol}")

    prices = prices.sort_values("Date").copy()
    prices["return_1d"] = prices["Close"].pct_change(1)
    prices["return_5d"] = prices["Close"].pct_change(5)
    prices["return_10d"] = prices["Close"].pct_change(10)
    prices["volatility_5"] = prices["return_1d"].rolling(5).std()
    prices["sma_3"] = prices["Close"].rolling(3).mean()
    prices["sma_5"] = prices["Close"].rolling(5).mean()
    prices["ema_10"] = _ema(prices["Close"], 10)
    prices["rsi_14"] = _rsi(prices["Close"], 14)
    prices["macd"], prices["macd_signal"], prices["macd_diff"] = _macd(prices["Close"])
    prices["bb_high"], _bb_low, prices["bb_width"], prices["bb_pos"] = _bollinger(prices["Close"], 20)
    prices["atr_14"] = _atr(prices["High"], prices["Low"], prices["Close"], 14)
    prices["stoch_k"] = _stoch_k(prices["High"], prices["Low"], prices["Close"], 14)
    prices["cci_20"] = _cci(prices["High"], prices["Low"], prices["Close"], 20)
    prices["cmf_20"] = _cmf(prices["High"], prices["Low"], prices["Close"], prices["Volume"], 20)
    prices["hl_spread"] = (prices["High"] - prices["Low"]) / prices["Close"].replace(0, np.nan)
    prices["williams_r"] = _williams_r(prices["High"], prices["Low"], prices["Close"], 14)
    prices["roc_10"] = _roc(prices["Close"], 10)
    prices["quarter"] = prices["Date"].dt.quarter.astype(float)

    market = fetch_market_history(prices["Date"].min(), prices["Date"].max())
    if market.empty:
        features = prices.copy()
        for col in ["spy_ret_5d", "spy_ret_10d", "spy_vol_20", "vix_level", "high_vol_regime"]:
            features[col] = 0.0
    else:
        features = prices.merge(market, on="Date", how="left")

    news_daily = score_and_aggregate_news(fetch_news_history(symbol))
    features = features.merge(news_daily, on="Date", how="left")

    fill_zero = [
        "news_count",
        "sentiment_conf_mean",
        "positive_ratio_lag3",
        "negative_ratio_lag3",
        "positive_ratio_roll5_mean",
        "sentiment_mean_roll5_mean",
        "sentiment_mean_lag3",
        "news_count_roll5_mean",
        "finnhub_count",
        "sentiment_mean",
        "spy_ret_5d",
        "spy_ret_10d",
        "spy_vol_20",
        "vix_level",
        "high_vol_regime",
    ]
    for col in fill_zero:
        if col not in features.columns:
            features[col] = 0.0
        features[col] = pd.to_numeric(features[col], errors="coerce").fillna(0.0)

    features["close_x_sentiment"] = features["Close"] * features["sentiment_mean"]
    features["volume_x_news_count"] = features["Volume"] * features["news_count"]
    features["rel_strength_vs_spy_5d"] = features["return_5d"] - features["spy_ret_5d"]
    features["rel_strength_vs_spy_10d"] = features["return_10d"] - features["spy_ret_10d"]
    features["volatility_vs_market"] = features["volatility_5"] / (features["spy_vol_20"] + 1e-6)

    if "Adj Close" not in features.columns:
        features["Adj Close"] = features["Close"]

    selected = features[["Date", "ticker", "company_id"] + feature_columns].copy()
    selected = selected.replace([np.inf, -np.inf], np.nan)

    for col in feature_columns:
        selected[col] = pd.to_numeric(selected[col], errors="coerce").ffill().bfill().fillna(0.0)

    return selected.sort_values("Date").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Prediction
# ---------------------------------------------------------------------------

def predict_symbol(symbol: str) -> PredictionResult:
    symbol = symbol.upper()
    metadata = load_model_metadata()
    seq_len = int(metadata["seq_len"])
    threshold = float(metadata.get("best_threshold", 0.5))
    feature_columns = metadata["feature_columns"]

    try:
        feature_df = build_model_features(symbol)
    except Exception as exc:
        logger.warning("Feature build failed for %s: %s", symbol, exc)
        return PredictionResult(symbol, None, None, threshold, "UNAVAILABLE", str(exc), 0)

    if len(feature_df) < seq_len:
        return PredictionResult(
            symbol,
            None,
            None,
            threshold,
            "UNAVAILABLE",
            f"Need at least {seq_len} feature rows, found {len(feature_df)}.",
            len(feature_df),
        )

    try:
        scaler = load_feature_scaler()
        model = load_prediction_model()

        sequence_df = feature_df[feature_columns].tail(seq_len).astype(np.float32)
        sequence_scaled = scaler.transform(sequence_df)
        x_seq = np.expand_dims(np.asarray(sequence_scaled, dtype=np.float32), axis=0)
        company_idx = np.array([[embedding_index_for_symbol(symbol)]], dtype=np.int32)

        raw_pred = model.predict(
            {
                "seq_input": x_seq,
                "company_id_input": company_idx,
            },
            verbose=0,
        )

        up_probability = float(np.ravel(raw_pred)[0])
        prediction = int(up_probability >= threshold)
        label = "UP" if prediction == 1 else "DOWN"

        return PredictionResult(
            symbol=symbol,
            up_probability=up_probability,
            prediction=prediction,
            threshold=threshold,
            label=label,
            quality_note="Live FinBERT + market features",
            feature_rows=len(feature_df),
        )
    except Exception as exc:
        logger.exception("Prediction failed for %s", symbol)
        return PredictionResult(
            symbol=symbol,
            up_probability=None,
            prediction=None,
            threshold=threshold,
            label="UNAVAILABLE",
            quality_note=f"Prediction failed: {exc}",
            feature_rows=len(feature_df),
        )





def score_unscored_news_articles(article_ids: list[int] | None = None, batch_size: int = 8) -> int:
    qs: QuerySet[NewsArticle] = NewsArticle.objects.filter(
        Q(finbert_scored_at__isnull=True)
        | Q(finbert_label="")
        | Q(finbert_confidence__isnull=True)
        | Q(finbert_signed_score__isnull=True)
    ).order_by("published_at")
    if article_ids:
        qs = qs.filter(id__in=article_ids)

    articles = list(qs)
    if not articles:
        return 0

    texts = [f"{article.headline}. {article.summary or ''}"[:3000] for article in articles]
    preds = score_finbert(texts, batch_size=batch_size)
    scored_at = timezone.now()

    for article, pred in zip(articles, preds):
        label, confidence, signed = pred
        article.finbert_label = str(label or "neutral").lower()
        article.finbert_confidence = float(confidence or 0.0)
        article.finbert_signed_score = float(signed or 0.0)
        article.finbert_scored_at = scored_at

    NewsArticle.objects.bulk_update(
        articles,
        ["finbert_label", "finbert_confidence", "finbert_signed_score", "finbert_scored_at"],
        batch_size=200,
    )
    return len(articles)


def _result_from_snapshot(symbol: str, snapshot: StockPredictionSnapshot | None) -> PredictionResult:
    if snapshot is None:
        return PredictionResult(
            symbol=symbol.upper(),
            up_probability=None,
            prediction=None,
            threshold=None,
            label="PENDING",
            quality_note="Prediction snapshot has not been refreshed yet.",
            feature_rows=0,
        )
    return PredictionResult(
        symbol=symbol.upper(),
        up_probability=snapshot.up_probability,
        prediction=snapshot.prediction,
        threshold=snapshot.threshold,
        label=snapshot.label,
        quality_note=snapshot.quality_note,
        feature_rows=snapshot.feature_rows,
    )


def get_prediction_snapshot(symbol: str) -> PredictionResult:
    symbol = symbol.upper().strip()
    stock = Stock.objects.filter(symbol=symbol).select_related("prediction_snapshot").first()
    snapshot = getattr(stock, "prediction_snapshot", None) if stock else None
    return _result_from_snapshot(symbol, snapshot)


def refresh_prediction_snapshot(symbol: str) -> PredictionResult:
    symbol = symbol.upper().strip()
    stock = Stock.objects.filter(symbol=symbol).first()
    if stock is None:
        return PredictionResult(symbol, None, None, None, "UNAVAILABLE", "Unknown symbol.", 0)

    result = predict_symbol(symbol)
    StockPredictionSnapshot.objects.update_or_create(
        stock=stock,
        defaults={
            "up_probability": result.up_probability,
            "prediction": result.prediction,
            "threshold": result.threshold,
            "label": result.label,
            "quality_note": result.quality_note,
            "feature_rows": result.feature_rows,
        },
    )
    return result


def refresh_prediction_snapshots(symbols: list[str] | None = None) -> int:
    target_symbols = [str(s).upper().strip() for s in (symbols or Stock.objects.values_list("symbol", flat=True)) if str(s).strip()]
    refreshed = 0
    for symbol in target_symbols:
        result = refresh_prediction_snapshot(symbol)
        if result.label != "UNAVAILABLE":
            refreshed += 1
    return refreshed


# ---------------------------------------------------------------------------
# Chart helpers
# ---------------------------------------------------------------------------

def _get_intraday_chart_df(symbol: str, selected_range: str) -> pd.DataFrame:
    if IntradayPrice is None:
        return pd.DataFrame()

    now = timezone.now()
    if selected_range == "1D":
        start_ts = now - pd.Timedelta(days=1)
    elif selected_range == "5D":
        start_ts = now - pd.Timedelta(days=5)
    else:
        start_ts = now - pd.Timedelta(days=30)

    rows = list(
        IntradayPrice.objects.filter(symbol=symbol.upper(), timestamp__gte=start_ts, timestamp__lte=now)
        .order_by("timestamp")
        .values("timestamp", "open", "high", "low", "close", "volume")
    )
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame.from_records(rows).rename(
        columns={
            "timestamp": "Date",
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
        }
    )
    df["Date"] = (
        pd.to_datetime(df["Date"], errors="coerce", utc=True)
        .dt.tz_convert("America/New_York")
        .dt.tz_localize(None)
    )
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["Date", "Open", "High", "Low", "Close"]).sort_values("Date").reset_index(drop=True)


def _get_daily_chart_df(symbol: str, selected_range: str) -> pd.DataFrame:
    period_map = {
        "3M": "3y",
        "6M": "3y",
        "1Y": "1y",
        "3Y": "3y",
        "5Y": "5y",
    }
    period = period_map.get(selected_range, "5y")
    return fetch_price_history(symbol, period=period)


def _add_rsi(series: pd.Series, window: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def build_stock_chart(
    symbol: str,
    selected_range: str = "5D",
    live_price: float | None = None,
    chart_mode: str = "candles",
) -> str:
    selected_range = normalize_chart_range(selected_range)
    chart_mode = normalize_chart_mode(chart_mode)

    if selected_range in INTRADAY_RANGES:
        chart_df = _get_intraday_chart_df(symbol, selected_range)
    else:
        chart_df = _get_daily_chart_df(symbol, selected_range)

    if chart_df.empty:
        return "<div class='alert alert-warning mb-0'>No chart data available for this range.</div>"

    chart_df = chart_df.copy().sort_values("Date").reset_index(drop=True)

    for col in ["Open", "High", "Low", "Close", "Volume"]:
        chart_df[col] = pd.to_numeric(chart_df[col], errors="coerce")
    chart_df["Date"] = pd.to_datetime(chart_df["Date"], errors="coerce")
    chart_df = chart_df.dropna(subset=["Date", "Open", "High", "Low", "Close"])
    if chart_df.empty:
        return "<div class='alert alert-warning mb-0'>No usable chart data available for this range.</div>"

    if live_price is not None and selected_range in {"1D", "5D"}:
        try:
            live_price = float(live_price)
            chart_df.loc[chart_df.index[-1], "Close"] = live_price
            chart_df.loc[chart_df.index[-1], "High"] = max(float(chart_df.loc[chart_df.index[-1], "High"]), live_price)
            chart_df.loc[chart_df.index[-1], "Low"] = min(float(chart_df.loc[chart_df.index[-1], "Low"]), live_price)
        except Exception:
            pass

    chart_df["SMA20"] = chart_df["Close"].rolling(20).mean()
    chart_df["SMA50"] = chart_df["Close"].rolling(50).mean()
    chart_df["SMA200"] = chart_df["Close"].rolling(200).mean()
    chart_df["EMA20"] = chart_df["Close"].ewm(span=20, adjust=False).mean()
    boll_mid = chart_df["Close"].rolling(20).mean()
    boll_std = chart_df["Close"].rolling(20).std()
    chart_df["Bollinger Upper"] = boll_mid + (2 * boll_std)
    chart_df["Bollinger Lower"] = boll_mid - (2 * boll_std)
    typical = (chart_df["High"] + chart_df["Low"] + chart_df["Close"]) / 3
    volume_nonzero = chart_df["Volume"].replace(0, np.nan)
    chart_df["VWAP"] = ((typical * volume_nonzero).cumsum() / volume_nonzero.cumsum()).replace([np.inf, -np.inf], np.nan)

    last_close = float(chart_df["Close"].iloc[-1])
    prev_close = float(chart_df["Close"].iloc[-2]) if len(chart_df) > 1 else last_close
    pct = ((last_close / prev_close) - 1) * 100 if prev_close else 0.0
    pct_color = "#16a34a" if pct >= 0 else "#dc2626"

    price_min = float(chart_df["Low"].min())
    price_max = float(chart_df["High"].max())
    indicator_candidates = [
        chart_df["SMA20"],
        chart_df["SMA50"],
        chart_df["SMA200"],
        chart_df["EMA20"],
        chart_df["Bollinger Upper"],
        chart_df["Bollinger Lower"],
        chart_df["VWAP"],
    ]
    for series in indicator_candidates:
        valid = series.dropna()
        if not valid.empty:
            price_min = min(price_min, float(valid.min()))
            price_max = max(price_max, float(valid.max()))
    price_pad = max((price_max - price_min) * 0.08, 1.5)

    fig = go.Figure()

    if chart_mode == "line":
        fig.add_trace(
            go.Scatter(
                x=chart_df["Date"],
                y=chart_df["Close"],
                mode="lines",
                name="Price",
                line=dict(width=2.7, color="#2563eb"),
                hovertemplate="%{x}<br>Close: $%{y:,.2f}<extra></extra>",
            )
        )
    else:
        fig.add_trace(
            go.Candlestick(
                x=chart_df["Date"],
                open=chart_df["Open"],
                high=chart_df["High"],
                low=chart_df["Low"],
                close=chart_df["Close"],
                name="Price",
                increasing_line_color="#16a34a",
                decreasing_line_color="#dc2626",
                increasing_fillcolor="#16a34a",
                decreasing_fillcolor="#dc2626",
            )
        )

    overlays = [
        ("SMA 20", chart_df["SMA20"], "#0ea5e9"),
        ("SMA 50", chart_df["SMA50"], "#f59e0b"),
        ("SMA 200", chart_df["SMA200"], "#64748b"),
        ("EMA 20", chart_df["EMA20"], "#7c3aed"),
        ("Bollinger Upper", chart_df["Bollinger Upper"], "#14b8a6"),
        ("Bollinger Lower", chart_df["Bollinger Lower"], "#14b8a6"),
    ]
    if selected_range in INTRADAY_RANGES and chart_df["VWAP"].notna().any():
        overlays.append(("VWAP", chart_df["VWAP"], "#ef4444"))

    for name, series, color in overlays:
        fig.add_trace(
            go.Scatter(
                x=chart_df["Date"],
                y=series,
                mode="lines",
                line=dict(width=1.7, color=color),
                name=name,
                visible="legendonly",
                hovertemplate=f"%{{x}}<br>{name}: $%{{y:,.2f}}<extra></extra>",
            )
        )

    x_min = chart_df["Date"].min()
    x_max = chart_df["Date"].max()
    if selected_range == "1D":
        default_start = x_max - pd.Timedelta(hours=7)
    elif selected_range == "5D":
        default_start = x_max - pd.Timedelta(days=5)
    elif selected_range == "1M":
        default_start = x_max - pd.Timedelta(days=30)
    elif selected_range == "3M":
        default_start = x_max - pd.Timedelta(days=92)
    elif selected_range == "6M":
        default_start = x_max - pd.Timedelta(days=183)
    elif selected_range == "1Y":
        default_start = x_max - pd.Timedelta(days=365)
    elif selected_range == "3Y":
        default_start = x_max - pd.Timedelta(days=365 * 3)
    else:
        default_start = x_min

    xaxis_kwargs = dict(
        type="date",
        showgrid=False,
        range=[default_start, x_max],
        showspikes=True,
        spikemode="across",
        spikesnap="cursor",
        spikecolor="#94a3b8",
        spikethickness=1,
        fixedrange=True,
    )
    if selected_range in {"1D", "5D"}:
        xaxis_kwargs["rangebreaks"] = [
            dict(bounds=["sat", "mon"]),
            dict(bounds=[16, 9.5], pattern="hour"),
        ]
    else:
        xaxis_kwargs["rangebreaks"] = [dict(bounds=["sat", "mon"])]

    fig.update_layout(
        template="plotly_white",
        height=620,
        margin=dict(l=58, r=26, t=70, b=34),
        paper_bgcolor="#ffffff",
        plot_bgcolor="#ffffff",
        hovermode="x unified",
        dragmode=False,
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1.0,
            bgcolor="rgba(255,255,255,0.88)",
            borderwidth=0,
        ),
        title=dict(
            text=f"{symbol.upper()} · {selected_range} · {chart_mode.title()} · ${last_close:,.2f} <span style='color:{pct_color}'>{pct:+.2f}%</span>",
            x=0.01,
            xanchor="left",
            font=dict(size=18, color="#0f172a"),
        ),
        xaxis_rangeslider_visible=False,
        uirevision=f"{symbol}-{selected_range}-{chart_mode}",
    )

    fig.update_yaxes(
        title_text="Price ($)",
        gridcolor="#e2e8f0",
        range=[price_min - price_pad, price_max + price_pad],
        fixedrange=True,
    )
    fig.update_xaxes(**xaxis_kwargs)

    fig.add_hline(
        y=last_close,
        line_color="#2563eb",
        line_dash="dot",
        opacity=0.35,
        annotation_text=f"Current ${last_close:,.2f}",
        annotation_position="top right",
        annotation_font=dict(size=11, color="#2563eb"),
    )

    return plot(
        fig,
        output_type="div",
        include_plotlyjs=False,
        config={
            "displayModeBar": False,
            "responsive": True,
            "scrollZoom": False,
            "doubleClick": False,
        },
    )
