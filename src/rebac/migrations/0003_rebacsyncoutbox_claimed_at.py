# T2.8: IN_FLIGHT outbox state + claim timestamp (reaper support).
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("rebac", "0002_alter_rebacsyncoutbox_options"),
    ]

    operations = [
        migrations.AddField(
            model_name="rebacsyncoutbox",
            name="claimed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="rebacsyncoutbox",
            name="status",
            field=models.CharField(
                choices=[
                    ("PEND", "⏳ Pending"),
                    ("INFL", "🔄 In Flight"),
                    ("SYNC", "✅ Synced"),
                    ("FAIL", "❌ Failed"),
                ],
                db_index=True,
                default="PEND",
                max_length=4,
            ),
        ),
    ]