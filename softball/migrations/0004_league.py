from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("softball", "0003_book"),
    ]

    operations = [
        migrations.AddField(
            model_name="season",
            name="league_url",
            field=models.URLField(blank=True, help_text="TeamSideline standings/schedule page.", max_length=1000),
        ),
        migrations.AddField(
            model_name="season",
            name="league_team_name",
            field=models.CharField(blank=True, help_text="This team's name on the league page.", max_length=100),
        ),
        migrations.AddField(
            model_name="season",
            name="league_name",
            field=models.CharField(blank=True, editable=False, max_length=200),
        ),
        migrations.AddField(
            model_name="season",
            name="league_standings",
            field=models.JSONField(blank=True, default=list, editable=False),
        ),
        migrations.AddField(
            model_name="season",
            name="league_synced_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="game",
            name="league_key",
            field=models.CharField(blank=True, default="", editable=False, max_length=40),
        ),
        migrations.AddConstraint(
            model_name="game",
            constraint=models.UniqueConstraint(
                condition=models.Q(("league_key", ""), _negated=True),
                fields=("season", "league_key"),
                name="unique_league_game_per_season",
            ),
        ),
    ]
