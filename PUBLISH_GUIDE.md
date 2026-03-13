# GitHub Publish Guide

## 1) Final local cleanup

Before your first public push, make sure your local project has:

- the final `tasks.py` you are actually running
- all Django migration files committed
- no stale duplicate scripts you no longer use
- a clean `README.md`, `.gitignore`, `.env.example`, and `requirements.txt`

## 2) Sanity-check the project locally

Run these commands from the project root:

```bash
python manage.py check
python manage.py makemigrations
python manage.py migrate
python load_stocks.py
python manage.py shell
```

In the Django shell, run:

```python
from news.tasks import refresh_watchlist_pipeline
print(refresh_watchlist_pipeline())
```

Then start the app:

```bash
python manage.py runserver
```

In a second terminal:

```bash
python manage.py qcluster
```

Verify:

- dashboard loads
- stock detail page loads
- refresh actions work
- charts switch between range and mode correctly
- watchlist add/remove works

## 3) Generate your exact dependency lock file

The included `requirements.txt` is a strong starter template.
Before publishing, the safest move is to generate the exact list from your working environment:

```bash
pip freeze > requirements.txt
```

Then quickly remove packages that are clearly unrelated to the project.

## 4) Create a clean Git repository

```bash
git init
git status
```

Review the status carefully before adding anything.
Make sure these are NOT included:

- `.env`
- `db.sqlite3`
- `.venv/`
- `__pycache__/`
- large/private model binaries you do not want public

## 5) First commit

```bash
git add .
git commit -m "Initial public release"
```

## 6) Create the GitHub repository

On GitHub:

- click **New repository**
- choose a repository name
- add a description
- keep it empty if you already committed locally
- choose Public or Private
- create the repo

## 7) Connect local repo to GitHub

Replace the URL below with your real GitHub repo URL:

```bash
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git branch -M main
git push -u origin main
```

## 8) Verify the public repo page

After push, check that the repo page shows:

- a readable README preview
- no secrets
- no local database
- no virtualenv files
- no accidental junk files

## 9) Fresh-clone test (most important)

Create a new folder outside your project and test the repo as if you were a stranger:

```bash
git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git
cd YOUR_REPO
python -m venv .venv
# activate the env
pip install -r requirements.txt
python manage.py check
python manage.py migrate
python load_stocks.py
python manage.py runserver
```

Also start:

```bash
python manage.py qcluster
```

If that works from a clean clone, the repo is ready.

## 10) Nice finishing touches

After the first successful push, improve the repo page with:

- 2–4 screenshots
- a short project demo GIF
- a short architecture section
- a roadmap section
- a license file

## 11) Recommended release note text

Example repo description:

> Django stock analysis dashboard that combines market prices, company news, background refresh pipelines, and ML prediction snapshots for a trained stock universe.
