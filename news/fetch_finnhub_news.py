import datetime
import os

import finnhub
from django.utils import timezone

from news.models import NewsArticle, Stock

FINNHUB_API_KEY = os.environ.get('FINNHUB_API_KEY', '').strip()


def fetch_and_save_company_news():
    if not FINNHUB_API_KEY:
        raise ValueError('FINNHUB_API_KEY is not set.')

    finnhub_client = finnhub.Client(api_key=FINNHUB_API_KEY)
    to_date = timezone.now().date()
    from_date = to_date - datetime.timedelta(days=7)
    from_str = from_date.strftime('%Y-%m-%d')
    to_str = to_date.strftime('%Y-%m-%d')

    print(f'--- Starting Finnhub Company News Sync from {from_str} to {to_str} ---')
    target_stocks = Stock.objects.all()
    if not target_stocks.exists():
        print('No stocks found in the database. Skipping news fetch.')
        return

    total_articles_saved = 0
    for stock in target_stocks:
        ticker = stock.symbol
        try:
            news_data = finnhub_client.company_news(ticker, _from=from_str, to=to_str)
        except finnhub.exceptions.FinnhubAPIException as e:
            print(f'Finnhub API Error for {ticker}: {e}')
            continue
        except Exception as e:
            print(f'Unexpected error for {ticker}: {e}')
            continue

        articles_saved_for_ticker = 0
        for article in news_data:
            headline = article.get('headline')
            summary = article.get('summary')
            url = article.get('url')
            source = article.get('source')
            timestamp = article.get('datetime')
            image_url = article.get('image')
            if not url or not headline or not summary or timestamp is None:
                continue
            try:
                published_dt = datetime.datetime.fromtimestamp(timestamp, tz=timezone.utc)
            except ValueError:
                continue

            news_obj, created = NewsArticle.objects.update_or_create(
                url=url,
                defaults={
                    'headline': headline,
                    'summary': summary,
                    'source': source,
                    'category': 'company',
                    'published_at': published_dt,
                    'image_url': image_url if image_url else None,
                    'finnhub_id': None,
                },
            )
            news_obj.stocks.add(stock)
            if created:
                articles_saved_for_ticker += 1

        total_articles_saved += articles_saved_for_ticker
        print(f'Saved/updated {articles_saved_for_ticker} new articles for {ticker}')

    print(f'--- Synchronization Complete. Total New Articles Saved: {total_articles_saved} ---')
