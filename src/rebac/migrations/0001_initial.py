from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="RebacSyncOutbox",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "action",
                    models.CharField(
                        choices=[("WRT", "🔗 Write"), ("DEL", "🔪 Delete")], max_length=3
                    ),
                ),
                ("user_id", models.CharField(max_length=255)),
                ("relation", models.CharField(max_length=100)),
                ("object_id", models.CharField(max_length=255)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("PEND", "⏳ Pending"),
                            ("SYNC", "✅ Synced"),
                            ("FAIL", "❌ Failed"),
                        ],
                        db_index=True,
                        default="PEND",
                        max_length=4,
                    ),
                ),
                ("retry_count", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "ReBac Sync Task",
                "verbose_name_plural": "ReBac Sync Tasks",
                "ordering": ("created_at",),
            },
        ),
    ]
