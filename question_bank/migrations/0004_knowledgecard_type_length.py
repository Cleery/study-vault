from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("question_bank", "0003_knowledge_card_core_content"),
    ]

    operations = [
        migrations.AlterField(
            model_name="knowledgecard",
            name="type",
            field=models.CharField(max_length=50),
        ),
    ]
