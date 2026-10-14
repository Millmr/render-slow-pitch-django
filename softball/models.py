"""Data model for the slow-pitch team manager.

``TeamMembership`` is the identity of a player on a team. Stats, lineups and
availability all hang off the roster entry rather than the global ``User``,
so a manager can add players before they register and link the account later.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q


class Team(models.Model):
    name = models.CharField(max_length=100)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_teams",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    # The logo is stored in the database as a small PNG (see softball/logos.py)
    # because Render's free web service has no persistent disk for uploads.
    logo = models.BinaryField(null=True, blank=True, editable=False)
    logo_content_type = models.CharField(max_length=50, blank=True, default="", editable=False)
    logo_updated_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self):
        return self.name

    @property
    def logo_version(self):
        """Changes whenever the logo does, so clients can cache by it."""
        # Uses only the timestamp so listing teams never loads the image bytes.
        return str(int(self.logo_updated_at.timestamp() * 1000)) if self.logo_updated_at else None


class Season(models.Model):
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="seasons")
    name = models.CharField(max_length=100, help_text="For example, Summer 2027.")
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-start_date", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["team", "name"], name="unique_season_name_per_team"),
            models.CheckConstraint(
                condition=Q(end_date__isnull=True) | Q(end_date__gte=F("start_date")),
                name="season_ends_after_it_starts",
            ),
        ]

    def __str__(self):
        return f"{self.team} – {self.name}"

    def clean(self):
        if self.end_date and self.start_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "A season can't end before it starts."})


class TeamMembership(models.Model):
    """A roster entry: one person's spot on a team (optionally for one season)."""

    class Role(models.TextChoices):
        MANAGER = "manager", "Manager"
        PLAYER = "player", "Player"

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="team_memberships",
        help_text="Empty until the player registers and the manager links their account.",
    )
    season = models.ForeignKey(
        Season,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="memberships",
        help_text="Leave empty for a membership that spans every season.",
    )
    display_name = models.CharField(max_length=100)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.PLAYER)
    jersey_number = models.PositiveIntegerField(null=True, blank=True)
    is_substitute = models.BooleanField(
        default=False,
        help_text="Fill-in player. Not expected to answer every availability request.",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["display_name", "id"]
        constraints = [
            # The same account can't hold two roster spots for the same season...
            models.UniqueConstraint(
                fields=["team", "user", "season"],
                condition=Q(user__isnull=False, season__isnull=False),
                name="unique_user_membership_per_season",
            ),
            # ...or two team-wide spots (NULL seasons aren't equal in SQL, so
            # this needs its own constraint).
            models.UniqueConstraint(
                fields=["team", "user"],
                condition=Q(user__isnull=False, season__isnull=True),
                name="unique_user_team_wide_membership",
            ),
        ]

    def __str__(self):
        return f"{self.display_name} ({self.team})"

    @property
    def is_manager(self):
        return self.role == self.Role.MANAGER

    def clean(self):
        if self.season_id and self.team_id and self.season.team_id != self.team_id:
            raise ValidationError({"season": "That season belongs to a different team."})


def eligible_memberships(season, active_only=True):
    """Roster entries who can take part in a game of ``season``.

    That is everyone on the team for that specific season plus anyone with a
    team-wide membership. Stat corrections pass ``active_only=False`` so a
    player who has since left the team keeps their numbers.
    """
    queryset = TeamMembership.objects.filter(team_id=season.team_id).filter(
        Q(season=season) | Q(season__isnull=True)
    )
    return queryset.filter(is_active=True) if active_only else queryset


class Game(models.Model):
    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    class Result(models.TextChoices):
        WIN = "W", "Win"
        LOSS = "L", "Loss"
        TIE = "T", "Tie"

    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="games")
    opponent_name = models.CharField(max_length=100)
    scheduled_at = models.DateTimeField()
    location = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SCHEDULED)
    team_score = models.PositiveIntegerField(null=True, blank=True)
    opponent_score = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["scheduled_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=~Q(status="completed") | Q(team_score__isnull=False, opponent_score__isnull=False),
                name="completed_game_has_scores",
            ),
        ]

    def __str__(self):
        return f"vs {self.opponent_name} ({self.scheduled_at:%Y-%m-%d})"

    @property
    def team(self):
        return self.season.team

    @property
    def result(self):
        """W/L/T derived from the scores. Never stored, so it can't contradict them."""
        if self.status != self.Status.COMPLETED or self.team_score is None or self.opponent_score is None:
            return None
        if self.team_score > self.opponent_score:
            return self.Result.WIN.value
        if self.team_score < self.opponent_score:
            return self.Result.LOSS.value
        return self.Result.TIE.value


class GameAvailability(models.Model):
    class Status(models.TextChoices):
        YES = "yes", "Yes"
        NO = "no", "No"
        UNSURE = "unsure", "Unsure"

    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="availability")
    membership = models.ForeignKey(TeamMembership, on_delete=models.CASCADE, related_name="availability")
    status = models.CharField(max_length=10, choices=Status.choices)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "game availability"
        constraints = [
            models.UniqueConstraint(fields=["game", "membership"], name="unique_availability_per_game"),
        ]

    def __str__(self):
        return f"{self.membership.display_name}: {self.status} ({self.game})"


class Lineup(models.Model):
    game = models.OneToOneField(Game, on_delete=models.CASCADE, related_name="lineup")
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Lineup {self.game}"


class LineupSpot(models.Model):
    class Position(models.TextChoices):
        PITCHER = "P", "Pitcher"
        CATCHER = "C", "Catcher"
        FIRST_BASE = "1B", "First base"
        SECOND_BASE = "2B", "Second base"
        THIRD_BASE = "3B", "Third base"
        SHORTSTOP = "SS", "Shortstop"
        LEFT_FIELD = "LF", "Left field"
        LEFT_CENTER = "LCF", "Left-center field"
        CENTER_FIELD = "CF", "Center field"
        RIGHT_CENTER = "RCF", "Right-center field"
        RIGHT_FIELD = "RF", "Right field"
        ROVER = "ROV", "Rover"
        EXTRA_HITTER = "EH", "Extra hitter"

    lineup = models.ForeignKey(Lineup, on_delete=models.CASCADE, related_name="spots")
    membership = models.ForeignKey(TeamMembership, on_delete=models.CASCADE, related_name="lineup_spots")
    batting_order = models.PositiveIntegerField()
    defensive_position = models.CharField(max_length=4, choices=Position.choices)

    class Meta:
        ordering = ["batting_order"]
        constraints = [
            models.UniqueConstraint(fields=["lineup", "batting_order"], name="unique_batting_slot"),
            models.UniqueConstraint(fields=["lineup", "membership"], name="unique_player_in_lineup"),
            # Every fielding position is filled once; any number of extra hitters.
            models.UniqueConstraint(
                fields=["lineup", "defensive_position"],
                condition=~Q(defensive_position="EH"),
                name="unique_defensive_position",
            ),
            models.CheckConstraint(condition=Q(batting_order__gte=1), name="batting_order_starts_at_one"),
        ]

    def __str__(self):
        return f"{self.batting_order}. {self.membership.display_name} ({self.defensive_position})"


class PlayerGameStats(models.Model):
    """One player's line for one game. Season totals are aggregated from these."""

    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="player_stats")
    membership = models.ForeignKey(TeamMembership, on_delete=models.CASCADE, related_name="game_stats")
    at_bats = models.PositiveIntegerField(default=0)
    hits = models.PositiveIntegerField(default=0)
    singles = models.PositiveIntegerField(default=0)
    doubles = models.PositiveIntegerField(default=0)
    triples = models.PositiveIntegerField(default=0)
    home_runs = models.PositiveIntegerField(default=0)
    walks = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "player game stats"
        constraints = [
            models.UniqueConstraint(fields=["game", "membership"], name="unique_stats_per_player_per_game"),
            models.CheckConstraint(
                condition=Q(hits=F("singles") + F("doubles") + F("triples") + F("home_runs")),
                name="hits_equal_hit_breakdown",
            ),
            models.CheckConstraint(condition=Q(hits__lte=F("at_bats")), name="hits_not_more_than_at_bats"),
        ]

    def __str__(self):
        return f"{self.membership.display_name} {self.hits}-for-{self.at_bats} ({self.game})"

    def clean(self):
        breakdown = self.singles + self.doubles + self.triples + self.home_runs
        if self.hits != breakdown:
            raise ValidationError({"hits": "Hits must equal singles + doubles + triples + home runs."})
        if self.hits > self.at_bats:
            raise ValidationError({"hits": "Hits can't exceed at-bats."})
