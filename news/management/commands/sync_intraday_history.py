from django.core.management.base import BaseCommand

from news.price_store import INTRADAY_INTERVAL, sync_many_intraday, sync_universe_symbols


class Command(BaseCommand):
    help = 'Download and store intraday OHLCV price data for trained symbols plus benchmarks.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=60)
        parser.add_argument('--interval', type=str, default=INTRADAY_INTERVAL)

    def handle(self, *args, **options):
        days = int(options['days'])
        interval = str(options['interval'])
        symbols = sync_universe_symbols()

        self.stdout.write(self.style.NOTICE(f'Syncing intraday {interval} data for {len(symbols)} symbols over {days} day(s)...'))
        results = sync_many_intraday(symbols, days=days, interval=interval)

        success = sum(1 for v in results.values() if v >= 0)
        failed = sum(1 for v in results.values() if v < 0)

        for symbol, count in results.items():
            if count >= 0:
                self.stdout.write(f'{symbol}: synced {count} row(s)')
            else:
                self.stdout.write(self.style.ERROR(f'{symbol}: failed'))

        self.stdout.write(self.style.SUCCESS(f'Done. Success={success}, Failed={failed}'))
