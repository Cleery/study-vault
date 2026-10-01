from django.core.management.base import BaseCommand, CommandError

from question_bank.exporting import import_bundle


class Command(BaseCommand):
    help = "导入版本化题库包"

    def add_arguments(self, parser):
        parser.add_argument("--input", required=True)

    def handle(self, *args, **options):
        try:
            import_bundle(options["input"])
        except Exception as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS("已导入题库包"))
