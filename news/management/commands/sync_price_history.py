from django.core.management.base import BaseCommand

from news.price_store import sync_many, sync_universe_symbols


class Command(BaseCommand):
    help = "Download and store historical OHLCV price data for trained symbols plus benchmarks."

    def add_arguments(self, parser):
        parser.add_argument("--years", type=int, default=5)

    def handle(self, *args, **options):
        years = int(options["years"])
        symbols = sync_universe_symbols()

        self.stdout.write(self.style.NOTICE(f"Syncing {len(symbols)} symbols for {years} year(s)..."))
        results = sync_many(symbols, years=years)

        success = sum(1 for v in results.values() if v >= 0)
        failed = sum(1 for v in results.values() if v < 0)

        for symbol, count in results.items():
            if count >= 0:
                self.stdout.write(f"{symbol}: synced {count} row(s)")
            else:
                self.stdout.write(self.style.ERROR(f"{symbol}: failed"))

        self.stdout.write(self.style.SUCCESS(f"Done. Success={success}, Failed={failed}"))