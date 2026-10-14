from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("softball", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="team",
            name="logo",
            field=models.BinaryField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="team",
            name="logo_content_type",
            field=models.CharField(blank=True, default="", editable=False, max_length=50),
        ),
        migrations.AddField(
            model_name="team",
            name="logo_updated_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
    ]
