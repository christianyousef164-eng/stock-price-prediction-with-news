# Stock Price Prediction With News

ML-powered stock analysis platform combining price data, news sentiment, watchlists, and interactive dashboards.

## Overview

This project is a Django-based stock intelligence dashboard that combines market prices, financial news, background refresh jobs, and model-driven prediction snapshots for a curated stock universe. It is designed as both a portfolio project and a practical end-to-end ML application with a real web interface.

## What the app does

- lets users create and manage a stock watchlist
- refreshes quotes, recent company news, intraday history, and prediction snapshots in the background
- shows a dashboard with movers, watchlist context, and a detailed news feed
- provides a stock detail page with interactive Plotly charts, range controls, and line/candlestick modes
- stores fetched articles locally for faster browsing and follow-up analysis
- supports optional FinBERT-based sentiment scoring for article text

## Main features

### Dashboard
- watchlist summary cards
- market pulse and coverage mix panels
- detailed news feed tied to tracked symbols
- quick refresh actions backed by Django Q

### Stock detail page
- interactive Plotly chart
- chart range controls such as M1 and longer windows
- chart mode switch between line and candlestick views
- company profile summary
- symbol-specific news and prediction snapshot

### Data and ML pipeline
- Finnhub-based company news and quote refresh
- yfinance-based historical and intraday price sync
- batched refresh jobs to avoid slow per-symbol sleeps
- snapshot-based prediction flow with optional live inference mode
- optional FinBERT scoring for sentiment enrichment

## Tech stack

- Python
- Django
- Django Q
- Finnhub API
- yfinance
- pandas / numpy
- Plotly
- TensorFlow / Keras
- transformers

## Project structure

```text
news4/                  Django project settings and URLs
news/                   Main app: models, views, tasks, ML integration
templates/              HTML templates
static/                 CSS, JS, images, icons, fonts
model_artifacts/        Model metadata and inference assets
manage.py               Django entry point
load_stocks.py          Loads the supported stock universe
```

## Requirements

- Python 3.11+
- pip
- a virtual environment
- optional: Finnhub API key for live company news and quote refresh

Install dependencies:

```bash
pip install -r requirements.txt
```

## Environment variables

Create a local `.env` from `.env.example` and fill in the values you need.

Core variables:

```env
DJANGO_SECRET_KEY=change-me
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost
FINNHUB_API_KEY=
USE_FINBERT=1
ENABLE_LIVE_PREDICTION=0
ENABLE_NEWS_SENTIMENT_ON_PAGE=0
ENABLE_COMPANY_PROFILE_FETCH=1
ALLOW_PRICE_NETWORK_FALLBACK=0
FINNHUB_BATCH_SIZE=5
FINNHUB_BATCH_PAUSE_SECONDS=0.15
```

## Local setup

### Windows (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
python manage.py migrate
python load_stocks.py
```

Run the app in two terminals:

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
python manage.py migrate
python load_stocks.py
```

Run the app in two terminals:

```bash
python manage.py runserver
```

```bash
python manage.py qcluster
```

## Typical workflow

1. start the Django server
2. start the Django Q worker with `qcluster`
3. open the dashboard at `/news/`
4. add symbols to the watchlist
5. trigger refresh actions or wait for scheduled jobs
6. open `/stock/<SYMBOL>/` for detailed chart and article context

## Model artifacts

The project expects model-related assets to be available in `model_artifacts/`. Depending on how you publish the repository, this can include metadata, lookup files, scalers, and model weights.

If you do not want to publish large or private artifacts, keep them out of Git and document how they can be regenerated or obtained.

## Known limitations

- live quote and company-news refresh depend on a valid Finnhub API key
- optional ML artifacts may be required for full prediction functionality
- background refresh jobs require `python manage.py qcluster` to be running
- this repository is configured for local development first, not production deployment

## Roadmap

- improve article-image handling for low-quality source thumbnails
- add tests for refresh jobs and prediction flows
- add a deployment recipe for a cloud host
- improve README screenshots and architecture diagrams
- add stronger validation for missing model artifacts and API configuration

## Screenshots

Add screenshots to this section after publishing so visitors can immediately see the UI.

Suggested images:
- dashboard overview
- stock detail chart view
- news feed view
- watchlist modal

## Why this project matters

This repository demonstrates an end-to-end ML product workflow:

- data ingestion
- feature preparation
- model-backed inference
- asynchronous background jobs
- persistence in Django models
- a usable interactive front end

## License

Add a license file if you want others to reuse or extend the code. MIT is a common choice for portfolio projects.
