import time

from django.core.management.base import BaseCommand

from question_bank.ai.service import process_next_ai_task


class Command(BaseCommand):
    help = "Process queued AI analysis tasks from the database."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--poll-seconds", type=float, default=2.0)

    def handle(self, *args, **options):
        once = options["once"]
        poll_seconds = max(0.1, options["poll_seconds"])
        while True:
            processed = process_next_ai_task()
            if once:
                return
            if processed is None:
                time.sleep(poll_seconds)
