# MoodStock: Stock Prediction Using Prices and News

A Django-based stock analysis dashboard that combines market data, company news, watchlists, background refresh jobs, and ML prediction snapshots for a trained stock universe.

## Features

- Watchlist-driven dashboard with quotes, movers, and prediction summaries
- Stock detail page with interactive Plotly charts and news context
- Background refresh pipelines powered by Django Q
- Finnhub-powered quote/news refresh with batching support
- Historical and intraday price sync using yfinance
- Optional FinBERT sentiment scoring for fetched articles
- Snapshot-based or live model inference modes

## Tech stack

- Python 3.11+
- Django
- Django Q
- Finnhub API
- yfinance
- pandas / numpy
- Plotly
- TensorFlow
- transformers (FinBERT)

## Project structure

```text
news4/                 # Django project package
news/                  # Django app package
templates/             # HTML templates
static/                # CSS / JS assets
model_artifacts/       # Model files and metadata (see below)
manage.py
load_stocks.py
```

## Required model artifacts

The ML parts of this project expect these files to exist either in `model_artifacts/` or at the project root:

- `model_metadata.json`
- `company_to_idx.json`
- `ticker_to_company_id.json`
- `feature_scaler.pkl`
- `final_stock_model.keras`

If you do not want to publish the large/private artifacts, keep them out of Git and document how to obtain them.

## Environment variables

This project reads configuration from shell environment variables and now also supports loading a local `.env` file.

Copy `.env.example` to `.env` and update the values.

Key variables:

- `DJANGO_SECRET_KEY`
- `DJANGO_DEBUG`
- `DJANGO_ALLOWED_HOSTS`
- `FINNHUB_API_KEY`
- `USE_FINBERT`
- `ENABLE_LIVE_PREDICTION`
- `ENABLE_NEWS_SENTIMENT_ON_PAGE`
- `ENABLE_COMPANY_PROFILE_FETCH`
- `ALLOW_PRICE_NETWORK_FALLBACK`
- `FINNHUB_BATCH_SIZE`
- `FINNHUB_BATCH_PAUSE_SECONDS`

## Local setup

### Windows (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
python manage.py makemigrations
python manage.py migrate
python load_stocks.py
python manage.py createsuperuser
```

Start the app in two terminals:

```powershell
python manage.py runserver
```

```powershell
python manage.py qcluster
```

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
python manage.py makemigrations
python manage.py migrate
python load_stocks.py
python manage.py createsuperuser
```

Start the app in two terminals:

```bash
python manage.py runserver
```

```bash
python manage.py qcluster
```

## Fresh-clone smoke test

Before publishing, verify a clean clone can do the following:

1. `pip install -r requirements.txt`
2. `python manage.py check`
3. `python manage.py migrate`
4. `python load_stocks.py`
5. `python manage.py runserver`
6. `python manage.py qcluster`
7. Open `/news/` and a `/stock/<SYMBOL>/` page

## Notes for GitHub

- Do **not** commit `.env`, `db.sqlite3`, `__pycache__/`, `.venv/`, or generated `staticfiles/`
- If your model weights are large, use Git LFS or keep them out of the repo
- Include your `news/migrations/` files in the repository
- Add screenshots to `README.md` after the first push if you want a stronger portfolio presentation

## Suggested README improvements after first push

- Add screenshots/GIFs of dashboard and stock detail page
- Add a short architecture diagram
- Add a "Known limitations" section
- Add a "Roadmap" section
