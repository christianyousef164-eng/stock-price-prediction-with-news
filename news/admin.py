from django.contrib import admin

from .models import HistoricalPrice, NewsArticle, Stock, StockPredictionSnapshot, StockQuote, UserStockList

try:
    from .models import IntradayPrice
except Exception:
    IntradayPrice = None


@admin.register(NewsArticle)
class NewsArticleAdmin(admin.ModelAdmin):
    list_display = ("headline", "source", "category", "published_at")
    list_filter = ("source", "category")
    search_fields = ("headline", "summary", "url")
    ordering = ("-published_at",)


@admin.register(Stock)
class StockAdmin(admin.ModelAdmin):
    list_display = ("symbol", "company_name")
    search_fields = ("symbol", "company_name")
    ordering = ("symbol",)


@admin.register(UserStockList)
class UserStockListAdmin(admin.ModelAdmin):
    list_display = ("user",)
    filter_horizontal = ("stocks",)


@admin.register(StockQuote)
class StockQuoteAdmin(admin.ModelAdmin):
    list_display = ("stock", "price", "change", "percent_change", "last_updated")
    list_filter = ("last_updated",)
    search_fields = ("stock__symbol", "stock__company_name")
    ordering = ("-last_updated",)


@admin.register(StockPredictionSnapshot)
class StockPredictionSnapshotAdmin(admin.ModelAdmin):
    list_display = ("stock", "label", "up_probability", "prediction", "computed_at")
    list_filter = ("label", "computed_at")
    search_fields = ("stock__symbol", "stock__company_name")
    ordering = ("-computed_at",)


@admin.register(HistoricalPrice)
class HistoricalPriceAdmin(admin.ModelAdmin):
    list_display = ("symbol", "date", "close", "volume", "source", "updated_at")
    list_filter = ("symbol", "source")
    search_fields = ("symbol",)
    ordering = ("symbol", "-date")


if IntradayPrice is not None:
    @admin.register(IntradayPrice)
    class IntradayPriceAdmin(admin.ModelAdmin):
        list_display = (
            "symbol",
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "source",
            "updated_at",
        )
        list_filter = ("symbol", "source")
        search_fields = ("symbol",)
        ordering = ("symbol", "-timestamp")