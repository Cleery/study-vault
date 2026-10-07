from django.core.management.base import BaseCommand, CommandError

from question_bank.ai.service import purge_expired_raw_responses


class Command(BaseCommand):
    help = "Delete expired raw AI provider responses while retaining analysis summaries."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=None)

    def handle(self, *args, **options):
        days = options["days"]
        if days is not None and days <= 0:
            raise CommandError("--days must be positive")
        removed = purge_expired_raw_responses(retention_days=days)
        self.stdout.write(self.style.SUCCESS(f"Cleared {removed} expired AI raw responses."))
