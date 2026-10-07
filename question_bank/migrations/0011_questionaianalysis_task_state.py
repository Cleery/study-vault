from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("question_bank", "0010_aireviewownership")]

    operations = [
        migrations.AddField(
            model_name="questionaianalysis",
            name="attempt_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="questionaianalysis",
            name="next_attempt_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="questionaianalysis",
            name="started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="questionaianalysis",
            index=models.Index(
                fields=["status", "next_attempt_at"],
                name="qb_ai_task_due_idx",
            ),
        ),
    ]
