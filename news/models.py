from django.contrib.auth.models import User
from django.db import models


class NewsArticle(models.Model):
    headline = models.CharField(max_length=255, verbose_name="Headline")
    summary = models.TextField(verbose_name="Summary")
    url = models.URLField(max_length=2000, unique=True, verbose_name="Article URL")
    source = models.CharField(max_length=100, verbose_name="Source")
    category = models.CharField(max_length=50, verbose_name="Category", default="general")
    published_at = models.DateTimeField(verbose_name="Published At")
    image_url = models.URLField(max_length=2000, blank=True, null=True, verbose_name="Image URL")
    finnhub_id = models.IntegerField(unique=True, null=True, blank=True, verbose_name="Finnhub ID")
    finbert_label = models.CharField(max_length=16, blank=True, default="")
    finbert_confidence = models.FloatField(null=True, blank=True)
    finbert_signed_score = models.FloatField(null=True, blank=True)
    finbert_scored_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    stocks = models.ManyToManyField("Stock", related_name="news_articles", blank=True, verbose_name="Related Stocks")

    class Meta:
        verbose_name = "News Article"
        verbose_name_plural = "News Articles"
        ordering = ["-published_at"]

    def __str__(self):
        return self.headline


class Stock(models.Model):
    symbol = models.CharField(max_length=16, unique=True, verbose_name="Ticker Symbol")
    company_name = models.CharField(max_length=255, verbose_name="Company Name")

    def __str__(self):
        return f"{self.symbol} - {self.company_name}"


class UserStockList(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, primary_key=True)
    stocks = models.ManyToManyField(Stock, related_name="watchlists")

    class Meta:
        verbose_name = "User Stock List"
        verbose_name_plural = "User Stock Lists"

    def __str__(self):
        return f"{self.user.username}'s Watchlist"


class StockQuote(models.Model):
    stock = models.OneToOneField(
        "Stock",
        on_delete=models.CASCADE,
        related_name="current_quote",
        verbose_name="Related Stock",
    )
    price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Current Price")
    change = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Change")
    percent_change = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Percent Change")
    last_updated = models.DateTimeField(auto_now=True, verbose_name="Last API Update")

    class Meta:
        verbose_name = "Stock Quote"
        verbose_name_plural = "Stock Quotes"

    def __str__(self):
        return f"{self.stock.symbol}: {self.price}"


class StockPredictionSnapshot(models.Model):
    stock = models.OneToOneField(
        "Stock",
        on_delete=models.CASCADE,
        related_name="prediction_snapshot",
        verbose_name="Related Stock",
    )
    up_probability = models.FloatField(null=True, blank=True)
    prediction = models.IntegerField(null=True, blank=True)
    threshold = models.FloatField(null=True, blank=True)
    label = models.CharField(max_length=32, default="UNAVAILABLE")
    quality_note = models.CharField(max_length=255, blank=True, default="")
    feature_rows = models.IntegerField(default=0)
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Stock Prediction Snapshot"
        verbose_name_plural = "Stock Prediction Snapshots"

    def __str__(self):
        return f"{self.stock.symbol}: {self.label}"


class HistoricalPrice(models.Model):
    symbol = models.CharField(max_length=16, db_index=True)
    stock = models.ForeignKey(
        "Stock",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="historical_prices",
    )
    date = models.DateField(db_index=True)
    open = models.FloatField()
    high = models.FloatField()
    low = models.FloatField()
    close = models.FloatField()
    volume = models.BigIntegerField(default=0)
    source = models.CharField(max_length=32, default="yfinance")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("symbol", "date")
        ordering = ["symbol", "date"]

    def __str__(self):
        return f"{self.symbol} {self.date} {self.close}"


class IntradayPrice(models.Model):
    symbol = models.CharField(max_length=16, db_index=True)
    stock = models.ForeignKey(
        "Stock",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="intraday_prices",
    )
    timestamp = models.DateTimeField(db_index=True)
    open = models.FloatField()
    high = models.FloatField()
    low = models.FloatField()
    close = models.FloatField()
    volume = models.BigIntegerField(default=0)
    source = models.CharField(max_length=32, default="yfinance")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("symbol", "timestamp")
        ordering = ["symbol", "timestamp"]

    def __str__(self):
        return f"{self.symbol} {self.timestamp} {self.close}"
