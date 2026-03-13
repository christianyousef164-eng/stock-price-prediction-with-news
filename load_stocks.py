import json
import os
from pathlib import Path

import django
from django.db import transaction

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'news4.settings')
django.setup()

from django.conf import settings  # noqa: E402
from news.models import Stock  # noqa: E402

ARTIFACT_CANDIDATES = [
    Path(settings.BASE_DIR) / 'model_artifacts' / 'ticker_to_company_id.json',
    Path(settings.BASE_DIR) / 'ticker_to_company_id.json',
    Path('/mnt/data/model_artifacts/ticker_to_company_id.json'),
    Path('/mnt/data/ticker_to_company_id.json'),
]


def find_ticker_mapping() -> Path:
    for path in ARTIFACT_CANDIDATES:
        if path.exists():
            return path
    raise FileNotFoundError('ticker_to_company_id.json was not found.')



def default_company_name(symbol: str) -> str:
    existing = Stock.objects.filter(symbol=symbol).values_list('company_name', flat=True).first()
    if existing:
        return existing
    return symbol



def load_stock_data() -> None:
    mapping_path = find_ticker_mapping()
    with open(mapping_path, 'r', encoding='utf-8') as f:
        ticker_map = json.load(f)

    created_count = 0
    updated_count = 0

    with transaction.atomic():
        for symbol in sorted(ticker_map.keys()):
            _, created = Stock.objects.update_or_create(
                symbol=symbol.upper(),
                defaults={'company_name': default_company_name(symbol.upper())},
            )
            if created:
                created_count += 1
            else:
                updated_count += 1

    print('\n--- Load Trained Stock Universe Summary ---')
    print(f'Mapping source: {mapping_path}')
    print(f'Total trained tickers: {len(ticker_map)}')
    print(f'Stocks created: {created_count}')
    print(f'Stocks updated: {updated_count}')
    print('Only the model-trained stock universe was loaded.')


if __name__ == '__main__':
    load_stock_data()
