from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views

app_name = "newsapp"

urlpatterns = [
    path("", views.welcome.as_view(), name="welcome"),
    path("news/", views.newspage.as_view(), name="newspage"),

    path("signup/", views.SignUpView.as_view(), name="signup"),
    path("login/", views.CustomLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(next_page="newsapp:welcome"), name="logout"),

    path("api/stock-quote/<str:symbol>/", views.get_stock_quote, name="get_stock_quote"),
    path("stock/<str:symbol>/", views.StockDetailView.as_view(), name="stock_detail"),

    path("api/search-stocks/", views.SearchStocksView.as_view(), name="api_search_stocks"),
    path("api/watchlist/", views.WatchlistAPIView.as_view(), name="api_watchlist"),

    path("api/refresh/dashboard/", views.RefreshDashboardAPIView.as_view(), name="api_refresh_dashboard"),
    path("api/refresh/stock/<str:symbol>/", views.RefreshStockAPIView.as_view(), name="api_refresh_stock"),
]