import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("softball", "0002_team_logo"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="teammembership",
            name="role",
            field=models.CharField(
                choices=[("manager", "Manager"), ("scorekeeper", "Scorekeeper"), ("player", "Player")],
                default="player",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="playergamestats",
            name="sac_flies",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.CreateModel(
            name="PlateAppearance",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("inning", models.PositiveSmallIntegerField()),
                ("sequence", models.PositiveIntegerField(help_text="Order within the game, starting at 1.")),
                (
                    "result",
                    models.CharField(
                        choices=[
                            ("1B", "Single"),
                            ("2B", "Double"),
                            ("3B", "Triple"),
                            ("HR", "Home run"),
                            ("BB", "Walk"),
                            ("OUT", "Out"),
                            ("SF", "Sacrifice fly"),
                        ],
                        max_length=3,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "game",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="plate_appearances",
                        to="softball.game",
                    ),
                ),
                (
                    "membership",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="plate_appearances",
                        to="softball.teammembership",
                    ),
                ),
                (
                    "recorded_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["sequence"],
                "constraints": [
                    models.UniqueConstraint(fields=("game", "sequence"), name="unique_plate_appearance_order"),
                    models.CheckConstraint(
                        condition=models.Q(("inning__gte", 1)), name="plate_appearance_inning_starts_at_one"
                    ),
                ],
            },
        ),
    ]
