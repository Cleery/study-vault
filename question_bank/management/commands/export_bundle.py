from django.core.management.base import BaseCommand

from question_bank.exporting import export_bundle


class Command(BaseCommand):
    help = "导出版本化题库包"

    def add_arguments(self, parser):
        parser.add_argument("--output", required=True)

    def handle(self, *args, **options):
        path = export_bundle(options["output"])
        self.stdout.write(self.style.SUCCESS(f"已导出 {path}"))
