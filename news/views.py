from __future__ import annotations

import json
import os
from functools import lru_cache
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.generic import CreateView, TemplateView, View
from django_q.tasks import async_task
import hashlib
import urllib.parse

from .ai_services import (
    PredictionResult,
    build_model_features,
    build_stock_chart,
    fetch_price_history,
    get_prediction_snapshot,
    normalize_chart_mode,
    normalize_chart_range,
    predict_symbol,
    trained_symbols,
)
from .forms import CustomUserCreationForm
from .models import NewsArticle, Stock, UserStockList

FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", getattr(settings, "FINNHUB_API_KEY", ""))
NY_TZ = ZoneInfo("America/New_York")


def _flag(name: str, default: bool = False) -> bool:
    return bool(getattr(settings, name, default))


def _safe_float(value):
    try:
        return float(value)
    except Exception:
        return None


def _human_number(value):
    value = _safe_float(value)
    if value is None:
        return "N/A"
    abs_value = abs(value)
    if abs_value >= 1_000_000_000_000:
        return f"${value / 1_000_000_000_000:.2f}T"
    if abs_value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if abs_value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if abs_value >= 1_000:
        return f"${value / 1_000:.2f}K"
    return f"${value:,.0f}"

def _article_theme(article, idx: int = 0) -> dict:
    category = str(getattr(article, "category", "") or "general").lower()
    source = str(getattr(article, "source", "") or "market").lower()
    seed = f"{getattr(article, 'headline', '')}|{source}|{category}|{idx}"
    bucket = int(hashlib.md5(seed.encode("utf-8")).hexdigest(), 16) % 8

    palettes = [
        {"bg1": "#e0f2fe", "bg2": "#dbeafe", "accent": "#2563eb", "text": "#0f172a", "label": "Market"},
        {"bg1": "#ecfeff", "bg2": "#cffafe", "accent": "#0891b2", "text": "#083344", "label": "News"},
        {"bg1": "#eef2ff", "bg2": "#e0e7ff", "accent": "#4f46e5", "text": "#1e1b4b", "label": "Signal"},
        {"bg1": "#f5f3ff", "bg2": "#ede9fe", "accent": "#7c3aed", "text": "#2e1065", "label": "Insight"},
        {"bg1": "#fef2f2", "bg2": "#fee2e2", "accent": "#dc2626", "text": "#450a0a", "label": "Risk"},
        {"bg1": "#ecfdf5", "bg2": "#dcfce7", "accent": "#16a34a", "text": "#052e16", "label": "Watchlist"},
        {"bg1": "#fff7ed", "bg2": "#ffedd5", "accent": "#ea580c", "text": "#431407", "label": "Macro"},
        {"bg1": "#f8fafc", "bg2": "#e2e8f0", "accent": "#334155", "text": "#0f172a", "label": "Brief"},
    ]

    theme = palettes[bucket].copy()
    if category in {"company", "earnings"}:
        theme["label"] = "Company"
    elif category in {"forex", "economy", "macro"}:
        theme["label"] = "Macro"
    elif category in {"general"}:
        theme["label"] = "Market"

    if "yahoo" in source:
        theme["label"] = "Yahoo"
    elif "benzinga" in source:
        theme["label"] = "Benzinga"
    elif "reuters" in source:
        theme["label"] = "Reuters"

    return theme


def _article_initials(article) -> str:
    source = str(getattr(article, "source", "") or "")
    category = str(getattr(article, "category", "") or "")
    base = source or category or "news"
    parts = [p for p in base.replace("-", " ").split() if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return base[:2].upper()


def _build_article_fallback_image(article, idx: int = 0) -> str:
    theme = _article_theme(article, idx)
    title = (getattr(article, "headline", "") or "Market update")[:42]
    label = theme["label"]
    initials = _article_initials(article)

    svg = f"""
    <svg xmlns="http://www.w3.org/2000/svg" width="1200" height="700" viewBox="0 0 1200 700">
      <defs>
        <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stop-color="{theme['bg1']}"/>
          <stop offset="100%" stop-color="{theme['bg2']}"/>
        </linearGradient>
      </defs>
      <rect width="1200" height="700" rx="36" fill="url(#bg)"/>
      <circle cx="170" cy="160" r="98" fill="{theme['accent']}" opacity="0.12"/>
      <circle cx="1010" cy="540" r="120" fill="{theme['accent']}" opacity="0.10"/>
      <rect x="72" y="74" width="170" height="54" rx="27" fill="#ffffff" opacity="0.92"/>
      <text x="157" y="109" text-anchor="middle" font-family="Arial, sans-serif" font-size="24" font-weight="700" fill="{theme['accent']}">{label}</text>
      <rect x="72" y="490" width="180" height="140" rx="28" fill="{theme['accent']}" opacity="0.92"/>
      <text x="162" y="575" text-anchor="middle" font-family="Arial, sans-serif" font-size="72" font-weight="800" fill="#ffffff">{initials}</text>
      <text x="320" y="300" font-family="Arial, sans-serif" font-size="58" font-weight="800" fill="{theme['text']}">{title}</text>
      <text x="320" y="360" font-family="Arial, sans-serif" font-size="28" font-weight="600" fill="{theme['text']}" opacity="0.78">Stored on site for faster analysis and watchlist context</text>
    </svg>
    """
    return "data:image/svg+xml;charset=utf-8," + urllib.parse.quote(svg)

def _preferred_article_image(article, idx: int = 0) -> tuple[str, str]:
    original = (getattr(article, "image_url", "") or "").strip()
    fallback = _build_article_fallback_image(article, idx)
    source = str(getattr(article, "source", "") or "").strip().lower()

    if source == "yahoo":
        return fallback, fallback

    return (original or fallback), fallback

def _decorate_article_visual(article, idx: int = 0):
    image_url = (getattr(article, "image_url", "") or "").strip()
    fallback_url = _build_article_fallback_image(article, idx)

    article.display_image_url = image_url if image_url else fallback_url
    article.fallback_image_url = fallback_url
    article.image_theme = _article_theme(article, idx)
    return article

def _prediction_result_for_symbol(symbol: str) -> PredictionResult:
    if _flag("ENABLE_LIVE_PREDICTION", False):
        return predict_symbol(symbol)
    return get_prediction_snapshot(symbol)


@lru_cache(maxsize=128)
def _fetch_company_profile(symbol: str, fallback_name: str = "") -> dict:
    profile = {
        "company_name": fallback_name or symbol,
        "sector": "N/A",
        "industry": "N/A",
        "exchange": "N/A",
        "market_cap": None,
        "market_cap_display": "N/A",
        "currency": "USD",
    }

    if not _flag("ENABLE_COMPANY_PROFILE_FETCH", False):
        return profile

    try:
        ticker = yf.Ticker(symbol)
        info = {}
        try:
            info = ticker.get_info() if hasattr(ticker, "get_info") else ticker.info
        except Exception:
            info = {}

        fast_info = {}
        try:
            fast_info = dict(getattr(ticker, "fast_info", {}) or {})
        except Exception:
            fast_info = {}

        market_cap = info.get("marketCap") or fast_info.get("marketCap")
        company_name = (
            info.get("longName")
            or info.get("shortName")
            or fallback_name
            or symbol
        )
        exchange = (
            info.get("fullExchangeName")
            or info.get("exchange")
            or fast_info.get("exchange")
            or "N/A"
        )

        profile.update(
            {
                "company_name": company_name,
                "sector": info.get("sector") or "N/A",
                "industry": info.get("industry") or "N/A",
                "exchange": exchange,
                "market_cap": market_cap,
                "market_cap_display": _human_number(market_cap) if market_cap else "N/A",
                "currency": info.get("currency") or fast_info.get("currency") or "USD",
            }
        )
    except Exception:
        pass

    return profile


def _market_session_snapshot() -> dict:
    now_utc = timezone.now()
    now_et = now_utc.astimezone(NY_TZ)
    minutes = now_et.hour * 60 + now_et.minute
    is_weekday = now_et.weekday() < 5
    is_open = is_weekday and (9 * 60 + 30) <= minutes < (16 * 60)

    return {
        "label": "Open" if is_open else "Closed",
        "tone": "positive" if is_open else "neutral",
        "detail": now_et.strftime("%a %H:%M ET"),
        "timezone_note": "Chart uses U.S. market time (ET)",
        "updated_utc": now_utc.strftime("%H:%M UTC"),
    }


def _build_price_stats(symbol: str, quote, profile: dict) -> list[dict]:
    history = fetch_price_history(symbol, period="1y")
    if history.empty:
        return []

    history = history.sort_values("Date").reset_index(drop=True)
    latest = history.iloc[-1]
    prev = history.iloc[-2] if len(history) > 1 else latest
    trailing = history.tail(252)

    current_price = _safe_float(getattr(quote, "price", None)) or _safe_float(latest.get("Close"))

    return [
        {
            "label": "Open",
            "value": f"${_safe_float(latest.get('Open')):,.2f}" if _safe_float(latest.get("Open")) is not None else "N/A",
            "note": "Latest session",
        },
        {
            "label": "Day High",
            "value": f"${_safe_float(latest.get('High')):,.2f}" if _safe_float(latest.get("High")) is not None else "N/A",
            "note": "Latest session",
        },
        {
            "label": "Day Low",
            "value": f"${_safe_float(latest.get('Low')):,.2f}" if _safe_float(latest.get("Low")) is not None else "N/A",
            "note": "Latest session",
        },
        {
            "label": "Prev Close",
            "value": f"${_safe_float(prev.get('Close')):,.2f}" if _safe_float(prev.get("Close")) is not None else "N/A",
            "note": "Prior session",
        },
        {
            "label": "Volume",
            "value": f"{int(_safe_float(latest.get('Volume')) or 0):,}",
            "note": "Latest session",
        },
        {
            "label": "52W Range",
            "value": (
                f"${float(trailing['Low'].min()):,.2f} - ${float(trailing['High'].max()):,.2f}"
                if not trailing.empty
                else "N/A"
            ),
            "note": "Trailing one year",
        },
        {
            "label": "Market Cap",
            "value": profile.get("market_cap_display", "N/A"),
            "note": profile.get("exchange", "N/A"),
        },
        {
            "label": "Live Price",
            "value": f"${current_price:,.2f}" if current_price is not None else "N/A",
            "note": profile.get("currency", "USD"),
        },
    ]


def _build_prediction_explanation(symbol: str, prediction_result) -> dict:
    confidence_label = "Unavailable"
    confidence_tone = "neutral"
    prob = prediction_result.up_probability

    if prob is not None:
        edge = abs(prob - 0.5)
        if edge >= 0.20:
            confidence_label, confidence_tone = "High confidence", "positive"
        elif edge >= 0.10:
            confidence_label, confidence_tone = "Moderate confidence", "warning"
        else:
            confidence_label, confidence_tone = "Low confidence", "neutral"

    if not _flag("ENABLE_LIVE_PREDICTION", False):
        direction = (
            "bullish"
            if prediction_result.prediction == 1
            else "bearish"
            if prediction_result.prediction == 0
            else "unclear"
        )

        summary = (
            f"Latest saved model snapshot leans {direction}. "
            f"{prediction_result.quality_note or 'Using cached prediction output for faster page loads.'}"
        )

        drivers = [
            {"label": "Saved prediction snapshot", "tone": "info"},
            {"label": "Stored FinBERT article scores", "tone": "positive"},
        ]

        if prediction_result.label == "PENDING":
            drivers.append({"label": "Run snapshot refresh to populate signal", "tone": "warning"})
        else:
            drivers.append({"label": "Background refresh keeps page responsive", "tone": "neutral"})

        return {
            "summary": summary,
            "confidence_label": confidence_label,
            "confidence_tone": confidence_tone,
            "drivers": drivers,
            "threshold": prediction_result.threshold,
        }

    drivers = []
    summary = "Feature diagnostics were unavailable for this symbol."
    try:
        feature_df = build_model_features(symbol)
        latest = feature_df.iloc[-1]

        sentiment = _safe_float(latest.get("sentiment_mean")) or 0.0
        ret_5d = _safe_float(latest.get("return_5d")) or 0.0
        rel_strength = _safe_float(latest.get("rel_strength_vs_spy_5d")) or 0.0
        news_count = int(_safe_float(latest.get("news_count")) or 0)
        vix_level = _safe_float(latest.get("vix_level")) or 0.0
        volatility = _safe_float(latest.get("volatility_5")) or 0.0

        if sentiment > 0.15:
            drivers.append({"label": "Positive news tone", "tone": "positive"})
        elif sentiment < -0.15:
            drivers.append({"label": "Negative news tone", "tone": "negative"})
        else:
            drivers.append({"label": "Neutral news tone", "tone": "neutral"})

        if ret_5d > 0.025:
            drivers.append({"label": "Strong 5D momentum", "tone": "positive"})
        elif ret_5d < -0.025:
            drivers.append({"label": "Weak 5D momentum", "tone": "negative"})
        else:
            drivers.append({"label": "Flat short-term trend", "tone": "neutral"})

        if rel_strength > 0.015:
            drivers.append({"label": "Outperforming SPY", "tone": "positive"})
        elif rel_strength < -0.015:
            drivers.append({"label": "Lagging SPY", "tone": "negative"})
        else:
            drivers.append({"label": "In line with SPY", "tone": "neutral"})

        if news_count >= 4:
            drivers.append({"label": f"{news_count} recent news items", "tone": "info"})
        elif news_count == 0:
            drivers.append({"label": "Quiet news backdrop", "tone": "neutral"})

        risk_driver = (
            "Elevated market volatility"
            if (vix_level >= 22 or volatility >= 0.02)
            else "Contained market volatility"
        )
        drivers.append(
            {"label": risk_driver, "tone": "warning" if "Elevated" in risk_driver else "positive"}
        )

        direction = "bullish" if prediction_result.prediction == 1 else "bearish"
        summary = (
            f"The model currently leans {direction} with {confidence_label.lower()}. "
            f"The strongest inputs come from recent news tone, short-term momentum, and relative strength versus the broader market."
        )
    except Exception:
        pass

    return {
        "summary": summary,
        "confidence_label": confidence_label,
        "confidence_tone": confidence_tone,
        "drivers": drivers[:5],
        "threshold": prediction_result.threshold,
    }


def _build_news_cards(articles) -> list[dict]:
    article_list = list(articles)
    if not article_list:
        return []

    now = timezone.now()
    cards = []

    for idx, article in enumerate(article_list):
        raw_label = str(getattr(article, "finbert_label", "") or "neutral").lower()
        confidence = float(getattr(article, "finbert_confidence", 0.0) or 0.0)
        signed = float(getattr(article, "finbert_signed_score", 0.0) or 0.0)

        if signed > 0.2:
            sentiment_label, sentiment_tone = "Bullish", "positive"
        elif signed < -0.2:
            sentiment_label, sentiment_tone = "Bearish", "negative"
        else:
            sentiment_label, sentiment_tone = "Neutral", "neutral"

        relevance = (
            "High relevance"
            if idx < 3
            else "Medium relevance"
            if idx < 8
            else "Watchlist context"
        )

        fallback_image_url = _build_article_fallback_image(article, idx)
        display_image_url = article.image_url if article.image_url else fallback_image_url

        cards.append(
            {
                "headline": article.headline,
                "summary": article.summary,
                "url": article.url,
                "source": article.source or "N/A",
                "published_at": article.published_at,
                "image_url": display_image_url,
                "fallback_image_url": fallback_image_url,
                "sentiment_label": sentiment_label,
                "sentiment_tone": sentiment_tone,
                "relevance_label": relevance,
                "confidence_pct": int(round(confidence * 100)),
                "in_model_window": bool(
                    article.published_at and article.published_at >= now - pd.Timedelta(days=7)
                ),
                "category": article.category or "general",
                "is_scored": bool(getattr(article, "finbert_scored_at", None)),
                "raw_finbert_label": raw_label,
            }
        )

    return cards


def get_stock_quote(request, symbol):
    if not FINNHUB_KEY:
        return JsonResponse(
            {"price": "0.00", "change": "0.00", "percent": "0.00%", "color": "gray"},
            status=503,
        )

    url = f"https://finnhub.io/api/v1/quote?symbol={symbol.upper()}&token={FINNHUB_KEY}"
    response = requests.get(url, timeout=8)
    response.raise_for_status()
    data = response.json()
    change_val = float(data.get("d", 0) or 0)

    return JsonResponse(
        {
            "price": f"{float(data.get('c', 0) or 0):.2f}",
            "change": f"{change_val:.2f}",
            "percent": f"{float(data.get('dp', 0) or 0):.2f}%",
            "color": "green" if change_val >= 0 else "red",
        }
    )


class StockDetailView(TemplateView):
    template_name = "stock_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        symbol = self.kwargs.get("symbol", "").upper()
        selected_range = normalize_chart_range(self.request.GET.get("range", "5D"))
        selected_chart_mode = normalize_chart_mode(self.request.GET.get("chart", "candles"))

        stock = get_object_or_404(Stock, symbol=symbol)
        quote = getattr(stock, "current_quote", None)
        prediction_result = _prediction_result_for_symbol(symbol)

        profile = _fetch_company_profile(symbol, stock.company_name)
        market_snapshot = _market_session_snapshot()

        live_price = _safe_float(getattr(quote, "price", None))
        prob = prediction_result.up_probability
        prob_pct = f"{prob * 100:.1f}%" if prob is not None else "Unavailable"

        prediction_badge = "Bullish" if prediction_result.prediction == 1 else "Bearish"
        if prediction_result.prediction is None:
            prediction_badge = "Unavailable"

        action = "Accumulate" if prediction_result.prediction == 1 else "Reduce"
        if prediction_result.prediction is None:
            action = "Wait"

        confidence = _build_prediction_explanation(symbol, prediction_result)
        related_news = stock.news_articles.all().order_by("-published_at")[:15]
        news_cards = _build_news_cards(related_news)

        in_watchlist = False
        if self.request.user.is_authenticated:
            user_list = getattr(self.request.user, "userstocklist", None)
            if user_list:
                in_watchlist = user_list.stocks.filter(symbol=symbol).exists()

        context.update(
            {
                "quote": quote,
                "stock": stock,
                "company_profile": profile,
                "market_snapshot": market_snapshot,
                "selected_range": selected_range,
                "range_options": ["1D", "5D", "1M", "3M", "6M", "1Y", "3Y", "5Y"],
                "selected_chart_mode": selected_chart_mode,
                "chart_mode_options": [("candles", "Candles"), ("line", "Line")],
                "chart": build_stock_chart(
                    symbol,
                    selected_range=selected_range,
                    live_price=live_price,
                    chart_mode=selected_chart_mode,
                ),
                "chart_indicator_options": [
                    "SMA 20",
                    "SMA 50",
                    "SMA 200",
                    "EMA 20",
                    "Bollinger Upper",
                    "Bollinger Lower",
                    "VWAP",
                ],
                "price_stats": _build_price_stats(symbol, quote, profile),
                "news_cards": news_cards,
                "prediction_result": prediction_result,
                "analysis_items": [
                    {
                        "title": "Signal",
                        "value": prediction_badge,
                        "caption": prediction_result.label,
                        "tone": "positive" if prediction_badge == "Bullish" else "negative" if prediction_badge == "Bearish" else "neutral",
                    },
                    {
                        "title": "Confidence",
                        "value": confidence["confidence_label"],
                        "caption": f"Up probability {prob_pct}",
                        "tone": confidence["confidence_tone"],
                    },
                    {
                        "title": "Suggested action",
                        "value": action,
                        "caption": "Model-guided posture",
                        "tone": "positive" if action == "Accumulate" else "warning" if action == "Wait" else "negative",
                    },
                ],
                "prediction_explainer": confidence,
                "watchlist_api_url": reverse_lazy("newsapp:api_watchlist"),
                "stock_search_url": reverse_lazy("newsapp:api_search_stocks"),
                "dashboard_url": reverse_lazy("newsapp:newspage"),
                "in_watchlist": in_watchlist,
            }
        )
        return context


class SignUpView(CreateView):
    form_class = CustomUserCreationForm
    template_name = "welcome.html"
    success_url = reverse_lazy("newsapp:login")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["show_modal"] = "sign-up"
        return context


class CustomLogoutView(LogoutView):
    next_page = reverse_lazy("newsapp:welcome")


class welcome(TemplateView):
    template_name = "welcome.html"


@method_decorator(login_required, name="dispatch")
class newspage(TemplateView):
    template_name = "index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        stock_containers = []
        try:
            user_list = UserStockList.objects.get(user=user)
            all_watchlist_stocks = user_list.stocks.all().select_related("current_quote")

            for stock in all_watchlist_stocks:
                quote = getattr(stock, "current_quote", None)
                if quote:
                    change_val = float(quote.change)
                    percent_change_val = float(quote.percent_change)
                    stock_containers.append(
                        {
                            "symbol": stock.symbol,
                            "company_name": stock.company_name,
                            "price": f"{quote.price:.2f}",
                            "change": f"{change_val:+.2f}",
                            "percentage": f"{percent_change_val:+.2f}%",
                            "last_update": quote.last_updated.strftime("%H:%M UTC"),
                            "change_direction_class": "stock-up" if change_val >= 0 else "stock-down",
                            "arrow_direction_class": "arrow-up" if change_val >= 0 else "arrow-down",
                            "tone": "positive" if change_val >= 0 else "negative",
                            "abs_move": abs(percent_change_val),
                        }
                    )
                else:
                    stock_containers.append(
                        {
                            "symbol": stock.symbol,
                            "company_name": stock.company_name,
                            "price": "--",
                            "change": "0.00",
                            "percentage": "0.00%",
                            "last_update": "Pending sync",
                            "change_direction_class": "stock-neutral",
                            "arrow_direction_class": "arrow-right",
                            "tone": "neutral",
                            "abs_move": 0,
                        }
                    )
        except UserStockList.DoesNotExist:
            pass

        stock_containers = sorted(stock_containers, key=lambda item: item.get("abs_move", 0), reverse=True)

        articles_qs = NewsArticle.objects.none()
        user_list = getattr(user, "userstocklist", None)
        if user_list:
            watchlist_stock_ids = user_list.stocks.values_list("id", flat=True)
            if watchlist_stock_ids.exists():
                articles_qs = (
                    NewsArticle.objects.filter(stocks__id__in=watchlist_stock_ids)
                    .distinct()
                    .order_by("-published_at")
                )

        if not articles_qs.exists():
            articles_qs = NewsArticle.objects.order_by("-published_at")

        articles = list(articles_qs[:36])

        for idx, article in enumerate(articles):
            display_image_url, fallback_image_url = _preferred_article_image(article, idx)
            article.display_image_url = display_image_url
            article.fallback_image_url = fallback_image_url

        lead_article = articles[0] if articles else None
        featured_articles = articles[1:5] if len(articles) > 1 else []

        category_counts = {}
        for article in articles:
            key = (article.category or "general").title()
            category_counts[key] = category_counts.get(key, 0) + 1
        categories = [
            {"category": key, "count": value}
            for key, value in sorted(category_counts.items(), key=lambda kv: kv[1], reverse=True)
        ]

        positive_count = sum(1 for item in stock_containers if item.get("tone") == "positive")
        negative_count = sum(1 for item in stock_containers if item.get("tone") == "negative")
        neutral_count = max(len(stock_containers) - positive_count - negative_count, 0)

        context.update(
            {
                "containers": stock_containers,
                "articles": articles,
                "lead_article": lead_article,
                "featured_articles": featured_articles,
                "categories": categories,
                "market_snapshot": _market_session_snapshot(),
                "watchlist_count": len(stock_containers),
                "positive_watchlist_count": positive_count,
                "negative_watchlist_count": negative_count,
                "neutral_watchlist_count": neutral_count,
                "stock_search_url": reverse_lazy("newsapp:api_search_stocks"),
                "watchlist_api_url": reverse_lazy("newsapp:api_watchlist"),
            }
        )
        return context


@method_decorator(login_required, name="dispatch")
class SearchStocksView(View):
    def get(self, request, *args, **kwargs):
        query = request.GET.get("q", "").strip()
        limit = request.GET.get("limit", 30)
        try:
            limit = int(limit)
        except ValueError:
            limit = 30

        trained = trained_symbols()
        qs = Stock.objects.filter(symbol__in=trained)
        if query:
            qs = qs.filter(Q(symbol__icontains=query) | Q(company_name__icontains=query)).order_by("symbol")[:limit]
        else:
            default_symbols = trained[:limit]
            qs = qs.filter(symbol__in=default_symbols).order_by("symbol")

        data = [{"symbol": stock.symbol, "company_name": stock.company_name} for stock in qs]
        return JsonResponse({"stocks": data})


@method_decorator(login_required, name="dispatch")
class WatchlistAPIView(View):
    def get(self, request, *args, **kwargs):
        user_list, _ = UserStockList.objects.get_or_create(user=request.user)
        stocks = user_list.stocks.values("symbol", "company_name").order_by("company_name")
        return JsonResponse({"watchlist": list(stocks)})

    def post(self, request, *args, **kwargs):
        try:
            data = json.loads(request.body)
            symbol = str(data.get("symbol", "")).upper().strip()
            if not symbol:
                return JsonResponse({"status": "error", "message": "Symbol not provided."}, status=400)

            stock = get_object_or_404(Stock, symbol=symbol)
            user_list, _ = UserStockList.objects.get_or_create(user=request.user)

            if user_list.stocks.filter(symbol=stock.symbol).exists():
                user_list.stocks.remove(stock)
                action = "removed"
            else:
                user_list.stocks.add(stock)
                action = "added"

            return JsonResponse(
                {
                    "status": "success",
                    "action": action,
                    "symbol": stock.symbol,
                    "company_name": stock.company_name,
                }
            )
        except json.JSONDecodeError:
            return JsonResponse({"status": "error", "message": "Invalid JSON."}, status=400)
        except Exception as exc:
            return JsonResponse({"status": "error", "message": str(exc)}, status=500)

@method_decorator(login_required, name="dispatch")
class RefreshDashboardAPIView(View):
    def post(self, request, *args, **kwargs):
        task_id = async_task("news.tasks.refresh_watchlist_pipeline")
        return JsonResponse(
            {
                "status": "queued",
                "task_id": str(task_id),
                "message": "Dashboard refresh queued.",
            }
        )


@method_decorator(login_required, name="dispatch")
class RefreshStockAPIView(View):
    def post(self, request, *args, **kwargs):
        symbol = str(kwargs.get("symbol", "")).upper().strip()
        if not symbol:
            return JsonResponse({"status": "error", "message": "Symbol missing."}, status=400)

        task_id = async_task("news.tasks.refresh_stock_pipeline", symbol)
        return JsonResponse(
            {
                "status": "queued",
                "task_id": str(task_id),
                "symbol": symbol,
                "message": f"Refresh queued for {symbol}.",
            }
        )


class CustomLoginView(LoginView):
    template_name = "welcome.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["show_modal"] = "log-in"
        return context