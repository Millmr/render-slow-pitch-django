from collections import Counter

from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import (
    Game,
    GameAvailability,
    LineupSpot,
    Season,
    Team,
    TeamMembership,
    eligible_memberships,
)

User = get_user_model()


def _duplicates(values):
    return sorted(value for value, count in Counter(values).items() if count > 1)


# --------------------------------------------------------------------------- teams


class TeamSerializer(serializers.ModelSerializer):
    my_role = serializers.SerializerMethodField()

    class Meta:
        model = Team
        fields = ["id", "name", "created_by", "created_at", "my_role"]
        read_only_fields = ["created_by", "created_at"]

    def get_my_role(self, team):
        request = self.context.get("request")
        if request is None:
            return None
        roles = set(
            team.memberships.filter(user=request.user, is_active=True).values_list("role", flat=True)
        )
        if TeamMembership.Role.MANAGER in roles:
            return TeamMembership.Role.MANAGER
        return TeamMembership.Role.PLAYER if roles else None


class SeasonSerializer(serializers.ModelSerializer):
    class Meta:
        model = Season
        fields = ["id", "team", "name", "start_date", "end_date", "is_active"]
        read_only_fields = ["team"]
        validators = []  # uniqueness is checked in validate() with a friendlier message

    def _team(self):
        return self.instance.team if self.instance else self.context["team"]

    def validate(self, attrs):
        start = attrs.get("start_date", getattr(self.instance, "start_date", None))
        end = attrs["end_date"] if "end_date" in attrs else getattr(self.instance, "end_date", None)
        if start and end and end < start:
            raise serializers.ValidationError({"end_date": "A season can't end before it starts."})

        name = attrs.get("name")
        if name:
            clash = Season.objects.filter(team=self._team(), name__iexact=name)
            if self.instance:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                raise serializers.ValidationError({"name": "This team already has a season with that name."})
        return attrs


class MembershipSerializer(serializers.ModelSerializer):
    """A roster entry. Managers link a registered account with ``username``."""

    season = serializers.PrimaryKeyRelatedField(
        queryset=Season.objects.all(), allow_null=True, required=False
    )
    display_name = serializers.CharField(max_length=100, required=False)
    username = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        allow_null=True,
        help_text="Username of a registered account to link to this roster spot. Blank unlinks it.",
    )
    account = serializers.SerializerMethodField(help_text="Linked account's username, if any.")

    class Meta:
        model = TeamMembership
        fields = [
            "id",
            "team",
            "season",
            "display_name",
            "role",
            "jersey_number",
            "is_substitute",
            "is_active",
            "account",
            "username",
            "created_at",
        ]
        read_only_fields = ["team", "created_at"]
        validators = []  # uniqueness is checked in validate() with a friendlier message

    def get_account(self, membership):
        return membership.user.username if membership.user_id else None

    def _team(self):
        return self.instance.team if self.instance else self.context["team"]

    def validate_season(self, season):
        if season is not None and season.team_id != self._team().id:
            raise serializers.ValidationError("That season belongs to a different team.")
        return season

    def validate(self, attrs):
        team = self._team()

        if "username" in attrs:
            username = (attrs.pop("username") or "").strip()
            if username:
                user = User.objects.filter(username__iexact=username).first()
                if user is None:
                    raise serializers.ValidationError({"username": "No account with that username."})
                attrs["user"] = user
            else:
                attrs["user"] = None

        user = attrs.get("user", self.instance.user if self.instance else None)
        season = attrs.get("season", self.instance.season if self.instance else None)

        if not self.instance and not attrs.get("display_name"):
            if user is None:
                raise serializers.ValidationError({"display_name": "Give the player a name."})
            attrs["display_name"] = user.get_full_name() or user.username

        if user is not None:
            clash = TeamMembership.objects.filter(team=team, user=user, season=season)
            if self.instance:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                raise serializers.ValidationError(
                    {"username": "That account already has a roster spot for this season."}
                )

        # Never leave a team without someone who can manage it.
        if self.instance and self.instance.is_manager and self.instance.is_active and self.instance.user_id:
            stays_manager = (
                attrs.get("role", self.instance.role) == TeamMembership.Role.MANAGER
                and attrs.get("is_active", True)
                and user is not None
            )
            if not stays_manager:
                other_managers = TeamMembership.objects.filter(
                    team=team, role=TeamMembership.Role.MANAGER, is_active=True, user__isnull=False
                ).exclude(pk=self.instance.pk)
                if not other_managers.exists():
                    raise serializers.ValidationError("A team needs at least one active manager.")
        return attrs


# --------------------------------------------------------------------------- games


class GameSerializer(serializers.ModelSerializer):
    # Completing a game goes through POST /games/{id}/result/ so a "completed"
    # game always has a score.
    status = serializers.ChoiceField(
        choices=[
            (Game.Status.SCHEDULED, Game.Status.SCHEDULED.label),
            (Game.Status.CANCELLED, Game.Status.CANCELLED.label),
        ],
        required=False,
        help_text="Use the result endpoint to mark a game completed.",
    )
    team_id = serializers.IntegerField(source="season.team_id", read_only=True)
    result = serializers.CharField(read_only=True)
    my_availability = serializers.SerializerMethodField()

    class Meta:
        model = Game
        fields = [
            "id",
            "season",
            "team_id",
            "opponent_name",
            "scheduled_at",
            "location",
            "status",
            "team_score",
            "opponent_score",
            "result",
            "my_availability",
        ]
        read_only_fields = ["season", "team_score", "opponent_score"]

    def get_my_availability(self, game):
        request = self.context.get("request")
        if request is None:
            return None
        record = (
            GameAvailability.objects.filter(game=game, membership__user=request.user, membership__is_active=True)
            .order_by("-updated_at")
            .first()
        )
        return record.status if record else None

    def update(self, instance, validated_data):
        new_status = validated_data.get("status")
        if instance.status == Game.Status.COMPLETED and new_status and new_status != Game.Status.COMPLETED:
            # Re-opening or cancelling a completed game clears its score.
            instance.team_score = None
            instance.opponent_score = None
        return super().update(instance, validated_data)


class GameResultSerializer(serializers.Serializer):
    team_score = serializers.IntegerField(min_value=0)
    opponent_score = serializers.IntegerField(min_value=0)


class AvailabilityUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=GameAvailability.Status.choices)
    membership_id = serializers.IntegerField(
        required=False,
        help_text="Managers only: set availability for another roster member (for example, to add a sub).",
    )


# --------------------------------------------------------------------------- lineups


class LineupSpotInputSerializer(serializers.Serializer):
    membership_id = serializers.IntegerField()
    batting_order = serializers.IntegerField(min_value=1)
    defensive_position = serializers.ChoiceField(choices=LineupSpot.Position.choices)


class LineupInputSerializer(serializers.Serializer):
    """Full replacement of a game's lineup. Needs ``season`` in context."""

    players = LineupSpotInputSerializer(many=True, allow_empty=True)

    def validate_players(self, players):
        ids = [p["membership_id"] for p in players]
        if dupes := _duplicates(ids):
            raise serializers.ValidationError(f"A player can only appear once in the lineup (membership {dupes}).")

        orders = [p["batting_order"] for p in players]
        if dupes := _duplicates(orders):
            raise serializers.ValidationError(f"Two players can't share batting slot {dupes}.")
        if sorted(orders) != list(range(1, len(orders) + 1)):
            raise serializers.ValidationError(f"Batting order must run from 1 to {len(orders)} with no gaps.")

        positions = [
            p["defensive_position"]
            for p in players
            if p["defensive_position"] != LineupSpot.Position.EXTRA_HITTER
        ]
        if dupes := _duplicates(positions):
            raise serializers.ValidationError(
                f"Each defensive position can only be filled once (except EH): {', '.join(dupes)}."
            )

        season = self.context["season"]
        valid_ids = set(eligible_memberships(season).filter(id__in=ids).values_list("id", flat=True))
        if invalid := [i for i in ids if i not in valid_ids]:
            raise serializers.ValidationError(f"Not on this season's active roster: membership {invalid}.")
        return players


# --------------------------------------------------------------------------- stats


class PlayerStatsInputSerializer(serializers.Serializer):
    membership_id = serializers.IntegerField()
    at_bats = serializers.IntegerField(min_value=0, default=0)
    hits = serializers.IntegerField(
        min_value=0,
        required=False,
        help_text="Optional. Computed from the hit breakdown; if sent it must match.",
    )
    singles = serializers.IntegerField(min_value=0, default=0)
    doubles = serializers.IntegerField(min_value=0, default=0)
    triples = serializers.IntegerField(min_value=0, default=0)
    home_runs = serializers.IntegerField(min_value=0, default=0)
    walks = serializers.IntegerField(min_value=0, default=0)

    def validate(self, attrs):
        breakdown = attrs["singles"] + attrs["doubles"] + attrs["triples"] + attrs["home_runs"]
        hits = attrs.get("hits", breakdown)
        if hits != breakdown:
            raise serializers.ValidationError(
                {"hits": f"Hits ({hits}) must equal singles + doubles + triples + home runs ({breakdown})."}
            )
        if hits > attrs["at_bats"]:
            raise serializers.ValidationError({"hits": "Hits can't exceed at-bats."})
        attrs["hits"] = hits
        return attrs


class GameStatsInputSerializer(serializers.Serializer):
    """Full replacement of a game's player stats. Needs ``season`` in context."""

    players = PlayerStatsInputSerializer(many=True, allow_empty=True)

    def validate_players(self, players):
        ids = [p["membership_id"] for p in players]
        if dupes := _duplicates(ids):
            raise serializers.ValidationError(
                f"Each player can only have one stat line per game (membership {dupes})."
            )
        # Include inactive members so a player who left keeps their numbers
        # when the manager corrects an old game.
        season = self.context["season"]
        roster = eligible_memberships(season, active_only=False)
        valid_ids = set(roster.filter(id__in=ids).values_list("id", flat=True))
        if invalid := [i for i in ids if i not in valid_ids]:
            raise serializers.ValidationError(f"Not on this season's roster: membership {invalid}.")
        return players
