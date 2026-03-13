import datetime
import math
import os
import time

import finnhub
from django.conf import settings
from django_q.models import Schedule
from django.utils import timezone
from finnhub.exceptions import FinnhubAPIException

from news.ai_services import refresh_prediction_snapshot, refresh_prediction_snapshots, score_unscored_news_articles
from news.models import NewsArticle, Stock, StockQuote
from news.price_store import INTRADAY_INTERVAL, sync_many, sync_many_intraday, upsert_intraday_history, upsert_price_history

def _current_finnhub_api_key() -> str:
    return str(getattr(settings, "FINNHUB_API_KEY", os.environ.get("FINNHUB_API_KEY", ""))).strip()


def _client():
    api_key = _current_finnhub_api_key()
    if not api_key:
        raise ValueError("FINNHUB_API_KEY is not set.")
    return finnhub.Client(api_key=api_key)


def _run_pipeline_step(step_name, func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except ValueError as exc:
        return f"Skipped {step_name}: {exc}"
    except Exception as exc:
        return f"Failed {step_name}: {exc}"


def _watchlist_symbols():
    return list(
        Stock.objects.filter(watchlists__isnull=False, symbol__isnull=False)
        .distinct()
        .values_list("symbol", flat=True)
    )

FINNHUB_BATCH_SIZE = max(1, int(getattr(settings, "FINNHUB_BATCH_SIZE", 5) or 5))
FINNHUB_BATCH_PAUSE_SECONDS = max(0.0, float(getattr(settings, "FINNHUB_BATCH_PAUSE_SECONDS", 0.15) or 0.0))


def _normalize_symbols(symbols):
    return [str(s).upper().strip() for s in symbols if str(s).strip()]


def _batched(items, batch_size):
    if batch_size <= 0:
        batch_size = 1
    for start in range(0, len(items), batch_size):
        yield items[start : start + batch_size]


def _batch_summary(total_items, batch_size, pause_seconds):
    if total_items <= 0:
        return "0 symbol(s) across 0 batch(es)."
    batch_count = math.ceil(total_items / max(1, batch_size))
    if pause_seconds > 0:
        return f"{total_items} symbol(s) across {batch_count} batch(es) with {pause_seconds:.2f}s pauses."
    return f"{total_items} symbol(s) across {batch_count} batch(es) with no pause."


def fetch_and_save_company_news(symbols=None, batch_size=FINNHUB_BATCH_SIZE, batch_pause_seconds=FINNHUB_BATCH_PAUSE_SECONDS):
    finnhub_client = _client()
    to_date = timezone.now().date()
    from_date = to_date - datetime.timedelta(days=7)

    requested_symbols = _normalize_symbols(symbols or [])
    if requested_symbols:
        target_stocks = list(Stock.objects.filter(symbol__in=requested_symbols).distinct().order_by("symbol"))
    else:
        target_stocks = list(Stock.objects.filter(watchlists__isnull=False).distinct().order_by("symbol"))

    touched_article_ids = set()
    processed = 0

    for batch_index, stock_batch in enumerate(_batched(target_stocks, batch_size), start=1):
        for stock in stock_batch:
            try:
                news_data = finnhub_client.company_news(
                    stock.symbol,
                    _from=from_date.strftime("%Y-%m-%d"),
                    to=to_date.strftime("%Y-%m-%d"),
                )
                processed += 1
                for article in news_data:
                    url = article.get("url")
                    if not url:
                        continue

                    published_ts = article.get("datetime")
                    if not published_ts:
                        continue

                    published_dt = timezone.make_aware(
                        datetime.datetime.fromtimestamp(published_ts),
                        datetime.timezone.utc,
                    )

                    news_obj, _ = NewsArticle.objects.update_or_create(
                        url=url,
                        defaults={
                            "headline": article.get("headline", "")[:255],
                            "summary": article.get("summary", ""),
                            "source": article.get("source", "Finnhub"),
                            "category": "company",
                            "published_at": published_dt,
                            "image_url": article.get("image"),
                        },
                    )
                    news_obj.stocks.add(stock)

                    if not news_obj.finbert_scored_at:
                        touched_article_ids.add(news_obj.id)

            except FinnhubAPIException as e:
                if "429" in str(e):
                    return (
                        f"Rate limited after {processed} news request(s) "
                        f"while processing {_batch_summary(len(target_stocks), batch_size, batch_pause_seconds)}"
                    )
            except Exception as e:
                print(f"Error fetching news for {stock.symbol}: {e}")

        if batch_pause_seconds > 0 and batch_index * batch_size < len(target_stocks):
            time.sleep(batch_pause_seconds)

    if touched_article_ids:
        score_unscored_news_articles(article_ids=list(touched_article_ids))

    return (
        f"Scored {len(touched_article_ids)} newly fetched article(s) from {processed} symbol(s) "
        f"using {_batch_summary(len(target_stocks), batch_size, batch_pause_seconds)}"
    )


def update_watchlist_quotes(symbols=None, batch_size=FINNHUB_BATCH_SIZE, batch_pause_seconds=FINNHUB_BATCH_PAUSE_SECONDS):
    finnhub_client = _client()

    requested_symbols = _normalize_symbols(symbols or [])
    if requested_symbols:
        target_stocks = list(Stock.objects.filter(symbol__in=requested_symbols).distinct().order_by("symbol"))
    else:
        target_stocks = list(Stock.objects.filter(watchlists__isnull=False).distinct().order_by("symbol"))

    updated = 0
    processed = 0

    for batch_index, stock_batch in enumerate(_batched(target_stocks, batch_size), start=1):
        for stock in stock_batch:
            try:
                quote = finnhub_client.quote(stock.symbol)
                processed += 1
                if quote.get("c"):
                    StockQuote.objects.update_or_create(
                        stock=stock,
                        defaults={
                            "price": quote.get("c"),
                            "change": quote.get("d", 0.0),
                            "percent_change": quote.get("dp", 0.0),
                        },
                    )
                    updated += 1
            except FinnhubAPIException as e:
                if "429" in str(e):
                    return (
                        f"Rate limited after {processed} quote request(s) and {updated} update(s) "
                        f"while processing {_batch_summary(len(target_stocks), batch_size, batch_pause_seconds)}"
                    )
            except Exception as e:
                print(f"Error updating quote for {stock.symbol}: {e}")

        if batch_pause_seconds > 0 and batch_index * batch_size < len(target_stocks):
            time.sleep(batch_pause_seconds)

    return (
        f"Updated {updated} quote(s) from {processed} symbol(s) "
        f"using {_batch_summary(len(target_stocks), batch_size, batch_pause_seconds)}"
    )


def update_prediction_snapshots(symbols=None):
    if symbols:
        target_symbols = [str(s).upper().strip() for s in symbols if str(s).strip()]
    else:
        target_symbols = _watchlist_symbols()

    if not target_symbols:
        return "No watchlist symbols found."

    refreshed = refresh_prediction_snapshots(target_symbols)
    return f"Refreshed {refreshed} prediction snapshot(s)."


def sync_intraday_watchlist_task(days=30, interval=INTRADAY_INTERVAL, symbols=None):
    if symbols:
        target_symbols = [str(s).upper().strip() for s in symbols if str(s).strip()]
    else:
        target_symbols = _watchlist_symbols()

    if not target_symbols:
        return "No watchlist symbols found."

    results = sync_many_intraday(target_symbols, days=days, interval=interval)
    success = sum(1 for value in results.values() if value >= 0)
    return f"Synced intraday history for {success} symbol(s)."


def sync_daily_history_task(years=5, symbols=None):
    if symbols:
        target_symbols = [str(s).upper().strip() for s in symbols if str(s).strip()]
    else:
        target_symbols = _watchlist_symbols()

    if not target_symbols:
        return "No watchlist symbols found."

    results = sync_many(target_symbols, years=years)
    success = sum(1 for value in results.values() if value >= 0)
    return f"Synced daily history for {success} symbol(s)."


def refresh_watchlist_pipeline():
    return {
        "news": _run_pipeline_step("news", fetch_and_save_company_news),
        "quotes": _run_pipeline_step("quotes", update_watchlist_quotes),
        "intraday": _run_pipeline_step(
            "intraday history",
            sync_intraday_watchlist_task,
            days=30,
            interval=INTRADAY_INTERVAL,
        ),
        "predictions": _run_pipeline_step("predictions", update_prediction_snapshots),
    }


def refresh_stock_pipeline(symbol: str):
    symbol = str(symbol).upper().strip()
    if not symbol:
        return {"status": "error", "message": "Symbol missing."}

    try:
        news_status = fetch_and_save_company_news(symbols=[symbol])
    except ValueError as exc:
        news_status = f"Skipped news: {exc}"

    try:
        quote_status = update_watchlist_quotes(symbols=[symbol])
    except ValueError as exc:
        quote_status = f"Skipped quotes: {exc}"

    try:
        intraday_rows = upsert_intraday_history(symbol, days=30, interval=INTRADAY_INTERVAL)
    except Exception as exc:
        intraday_rows = f"failed: {exc}"

    try:
        daily_rows = upsert_price_history(symbol, years=5)
    except Exception as exc:
        daily_rows = f"failed: {exc}"

    prediction = refresh_prediction_snapshot(symbol)

    return {
        "status": "ok",
        "symbol": symbol,
        "news": news_status,
        "quotes": quote_status,
        "intraday_rows": intraday_rows,
        "daily_rows": daily_rows,
        "prediction_label": prediction.label,
    }


def ensure_default_schedules():
    now = timezone.now()

    defaults = [
        {
            "name": "Watchlist pipeline every 10 min",
            "func": "news.tasks.refresh_watchlist_pipeline",
            "schedule_type": Schedule.MINUTES,
            "minutes": 10,
        },
        {
            "name": "Watchlist quotes every 2 min",
            "func": "news.tasks.update_watchlist_quotes",
            "schedule_type": Schedule.MINUTES,
            "minutes": 2,
        },
        {
            "name": "Prediction snapshots every 15 min",
            "func": "news.tasks.update_prediction_snapshots",
            "schedule_type": Schedule.MINUTES,
            "minutes": 15,
        },
        {
            "name": "Daily history nightly",
            "func": "news.tasks.sync_daily_history_task",
            "schedule_type": Schedule.CRON,
            "cron": "30 23 * * 1-5",
        },
    ]

    created_or_updated = []

    for item in defaults:
        schedule, created = Schedule.objects.get_or_create(
            name=item["name"],
            defaults={
                "func": item["func"],
                "schedule_type": item["schedule_type"],
                "minutes": item.get("minutes"),
                "cron": item.get("cron"),
                "next_run": now + datetime.timedelta(minutes=1),
                "repeats": -1,
            },
        )

        if not created:
            schedule.func = item["func"]
            schedule.schedule_type = item["schedule_type"]
            schedule.minutes = item.get("minutes")
            schedule.cron = item.get("cron")
            if schedule.next_run is None:
                schedule.next_run = now + datetime.timedelta(minutes=1)
            schedule.repeats = -1
            schedule.save()

        created_or_updated.append(schedule.name)

    return created_or_updated