"""REST API for teams, rosters, schedules, lineups and stats.

Access pattern used throughout:
  * querysets only contain objects from teams the user actively belongs to
    (anything else is a 404);
  * reads are open to every member of the team;
  * writes call ``require_manager`` (403 for players), except availability,
    where players may update their own response.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import F, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.http import quote_etag
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from . import stats as stats_service
from .logos import process_logo
from .models import (
    Game,
    GameAvailability,
    Lineup,
    LineupSpot,
    PlayerGameStats,
    Season,
    Team,
    TeamMembership,
    eligible_memberships,
)
from .permissions import is_manager, require_manager, visible_team_ids
from .serializers import (
    AvailabilityUpdateSerializer,
    GameResultSerializer,
    GameSerializer,
    GameStatsInputSerializer,
    LineupInputSerializer,
    MembershipSerializer,
    SeasonSerializer,
    TeamLogoSerializer,
    TeamSerializer,
)

TRUTHY = {"1", "true", "yes", "on"}


def _int_param(value, name):
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValidationError({name: "Must be an integer."})


# --------------------------------------------------------------------------- teams


class TeamViewSet(viewsets.ModelViewSet):
    """
    list:     Teams the current user belongs to.
    create:   Create a team; the creator becomes its first manager.
    roster:   GET the roster (``?season=<id>``, ``?include_inactive=1``); POST to add a player.
    seasons:  GET the team's seasons; POST to add one.
    """

    serializer_class = TeamSerializer
    http_method_names = ["get", "post", "put", "patch", "delete", "head", "options"]

    def get_queryset(self):
        # The logo bytes are only loaded by the logo endpoint.
        queryset = Team.objects.filter(id__in=visible_team_ids(self.request.user))
        return queryset if self.action == "logo" else queryset.defer("logo")

    def update(self, request, *args, **kwargs):
        # Team details are edited with PATCH; PUT is only for the logo upload.
        if not kwargs.get("partial"):
            return self.http_method_not_allowed(request, *args, **kwargs)
        return super().update(request, *args, **kwargs)

    def perform_create(self, serializer):
        user = self.request.user
        with transaction.atomic():
            team = serializer.save(created_by=user)
            TeamMembership.objects.create(
                team=team,
                user=user,
                display_name=user.get_full_name() or user.username,
                role=TeamMembership.Role.MANAGER,
            )

    def perform_update(self, serializer):
        require_manager(self.request.user, serializer.instance)
        serializer.save()

    def perform_destroy(self, instance):
        require_manager(self.request.user, instance)
        instance.delete()

    @action(
        detail=True,
        methods=["get", "put", "delete"],
        serializer_class=TeamLogoSerializer,
        parser_classes=[MultiPartParser, FormParser],
    )
    def logo(self, request, pk=None):
        """GET the logo image; PUT a multipart ``logo`` file or DELETE it (managers)."""
        team = self.get_object()

        if request.method == "GET":
            if not team.logo:
                return Response({"detail": "This team has no logo."}, status=status.HTTP_404_NOT_FOUND)
            etag = quote_etag(team.logo_version or "")
            if request.headers.get("If-None-Match") == etag:
                return HttpResponse(status=status.HTTP_304_NOT_MODIFIED)
            response = HttpResponse(bytes(team.logo), content_type=team.logo_content_type or "image/png")
            response["ETag"] = etag
            # Private: only team members can see it. The ?v= in the URL changes with the logo.
            response["Cache-Control"] = "private, max-age=86400"
            return response

        require_manager(request.user, team)
        if request.method == "DELETE":
            team.logo, team.logo_content_type, team.logo_updated_at = None, "", None
            team.save(update_fields=["logo", "logo_content_type", "logo_updated_at"])
            return Response(status=status.HTTP_204_NO_CONTENT)

        serializer = TeamLogoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            png = process_logo(serializer.validated_data["logo"])
        except DjangoValidationError as exc:
            raise ValidationError({"logo": exc.messages})
        team.logo, team.logo_content_type, team.logo_updated_at = png, "image/png", timezone.now()
        team.save(update_fields=["logo", "logo_content_type", "logo_updated_at"])
        return Response(TeamSerializer(team, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["get", "post"], serializer_class=MembershipSerializer)
    def roster(self, request, pk=None):
        team = self.get_object()
        context = self.get_serializer_context() | {"team": team}

        if request.method == "POST":
            require_manager(request.user, team)
            serializer = MembershipSerializer(data=request.data, context=context)
            serializer.is_valid(raise_exception=True)
            serializer.save(team=team)
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        memberships = team.memberships.select_related("user")
        if str(request.query_params.get("include_inactive", "")).lower() not in TRUTHY:
            memberships = memberships.filter(is_active=True)
        if "season" in request.query_params:
            season = get_object_or_404(team.seasons, pk=_int_param(request.query_params["season"], "season"))
            memberships = memberships.filter(Q(season=season) | Q(season__isnull=True))
        return Response(MembershipSerializer(memberships, many=True, context=context).data)

    @action(detail=True, methods=["get", "post"], serializer_class=SeasonSerializer)
    def seasons(self, request, pk=None):
        team = self.get_object()
        context = self.get_serializer_context() | {"team": team}

        if request.method == "POST":
            require_manager(request.user, team)
            serializer = SeasonSerializer(data=request.data, context=context)
            serializer.is_valid(raise_exception=True)
            serializer.save(team=team)
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        return Response(SeasonSerializer(team.seasons.all(), many=True, context=context).data)


# --------------------------------------------------------------------------- seasons


class SeasonViewSet(
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """
    games:        GET the season's schedule (``?status=scheduled``); POST to schedule a game.
    leaderboard:  Ranked season stats. ``?sort=`` one of the whitelisted stats.
    """

    serializer_class = SeasonSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        return Season.objects.filter(team_id__in=visible_team_ids(self.request.user)).select_related("team")

    def perform_update(self, serializer):
        require_manager(self.request.user, serializer.instance.team)
        serializer.save()

    def perform_destroy(self, instance):
        require_manager(self.request.user, instance.team)
        instance.delete()

    @action(detail=True, methods=["get", "post"], serializer_class=GameSerializer)
    def games(self, request, pk=None):
        season = self.get_object()
        context = self.get_serializer_context()

        if request.method == "POST":
            require_manager(request.user, season.team)
            serializer = GameSerializer(data=request.data, context=context)
            serializer.is_valid(raise_exception=True)
            serializer.save(season=season)
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        games = season.games.select_related("season")
        status_filter = request.query_params.get("status")
        if status_filter:
            if status_filter not in Game.Status.values:
                raise ValidationError({"status": f"Choose one of: {', '.join(Game.Status.values)}."})
            games = games.filter(status=status_filter)
        return Response(GameSerializer(games, many=True, context=context).data)

    @action(detail=True, methods=["get"])
    def leaderboard(self, request, pk=None):
        season = self.get_object()
        sort = request.query_params.get("sort", "batting_average")
        if sort not in stats_service.LEADERBOARD_SORTS:
            raise ValidationError({"sort": f"Choose one of: {', '.join(stats_service.LEADERBOARD_SORTS)}."})
        return Response(
            {
                "season": season.id,
                "sort": sort,
                "sort_options": list(stats_service.LEADERBOARD_SORTS),
                "obp_note": "Simplified OBP: (H + BB) / (AB + BB).",
                "players": stats_service.leaderboard(season, sort),
            }
        )


# --------------------------------------------------------------------------- memberships


class MembershipViewSet(mixins.RetrieveModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """
    Roster entries. Managers PATCH role, jersey number, substitute flag,
    linked account (``username``) or ``is_active``. Deactivate players instead
    of deleting them so their stats stay intact.

    stats: a player's season line and game log (``?season=<id>``).
    """

    serializer_class = MembershipSerializer
    http_method_names = ["get", "patch", "head", "options"]

    def get_queryset(self):
        return TeamMembership.objects.filter(team_id__in=visible_team_ids(self.request.user)).select_related(
            "team", "user", "season"
        )

    def perform_update(self, serializer):
        require_manager(self.request.user, serializer.instance.team)
        serializer.save()

    @action(detail=True, methods=["get"])
    def stats(self, request, pk=None):
        membership = self.get_object()
        team_seasons = membership.team.seasons.all()
        if "season" in request.query_params:
            season = get_object_or_404(team_seasons, pk=_int_param(request.query_params["season"], "season"))
        else:
            season = membership.season or team_seasons.filter(is_active=True).first() or team_seasons.first()

        if season:
            totals = stats_service.player_season_line(membership, season)
            games = stats_service.game_log(membership, season)
        else:
            totals, games = stats_service.empty_line(membership), []
        return Response(
            {
                "membership": MembershipSerializer(membership, context=self.get_serializer_context()).data,
                "season": season.id if season else None,
                "totals": totals,
                "games": games,
            }
        )


# --------------------------------------------------------------------------- games


def _availability_payload(game, user):
    roster = list(eligible_memberships(game.season).select_related("user"))
    roster_ids = {m.id for m in roster}
    records = {
        record.membership_id: record
        for record in GameAvailability.objects.filter(game=game, membership_id__in=roster_ids)
    }

    responses, missing = [], []
    summary = {choice: 0 for choice in GameAvailability.Status.values}
    for member in roster:
        entry = {
            "membership_id": member.id,
            "display_name": member.display_name,
            "jersey_number": member.jersey_number,
            "is_substitute": member.is_substitute,
        }
        record = records.get(member.id)
        if record:
            summary[record.status] += 1
            responses.append(entry | {"status": record.status, "updated_at": record.updated_at})
        elif not member.is_substitute:
            # Subs only show up once they've been asked and have answered.
            missing.append(entry)
    summary["no_response"] = len(missing)

    mine = next((m.id for m in roster if m.user_id == user.id), None)
    return {
        "game": game.id,
        "my_membership_id": mine,
        "summary": summary,
        "responses": responses,
        "missing": missing,
    }


def _lineup_payload(game, warnings=None):
    lineup = Lineup.objects.filter(game=game).first()
    spots = lineup.spots.select_related("membership").order_by("batting_order") if lineup else []
    return {
        "game": game.id,
        "updated_at": lineup.updated_at if lineup else None,
        "players": [
            {
                "membership_id": spot.membership_id,
                "display_name": spot.membership.display_name,
                "jersey_number": spot.membership.jersey_number,
                "batting_order": spot.batting_order,
                "defensive_position": spot.defensive_position,
            }
            for spot in spots
        ],
        "warnings": warnings or [],
    }


def _game_stats_payload(game):
    rows = PlayerGameStats.objects.filter(game=game).select_related("membership").order_by(
        "membership__display_name", "membership_id"
    )
    return {
        "game": game.id,
        "status": game.status,
        "counts_toward_season": game.status == Game.Status.COMPLETED,
        "players": [stats_service.game_line(row) for row in rows],
    }


class GameViewSet(
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """
    PATCH edits details or sets ``status`` to scheduled/cancelled.

    availability:  GET responses + who hasn't answered; PATCH ``{"status": "yes"}``.
    lineup:        GET; PUT the whole lineup ``{"players": [...]}`` (managers).
    result:        POST ``{"team_score", "opponent_score"}`` to complete the game (managers).
    stats:         GET; PUT every player's line for the game (managers).
    """

    serializer_class = GameSerializer
    http_method_names = ["get", "post", "put", "patch", "delete", "head", "options"]

    def get_queryset(self):
        return Game.objects.filter(season__team_id__in=visible_team_ids(self.request.user)).select_related(
            "season__team"
        )

    def update(self, request, *args, **kwargs):
        # Game details are only edited with PATCH; PUT is reserved for the
        # bulk lineup/stats actions.
        if not kwargs.get("partial"):
            return self.http_method_not_allowed(request, *args, **kwargs)
        return super().update(request, *args, **kwargs)

    def perform_update(self, serializer):
        require_manager(self.request.user, serializer.instance.team)
        serializer.save()

    def perform_destroy(self, instance):
        require_manager(self.request.user, instance.team)
        instance.delete()

    @action(detail=True, methods=["get", "patch"], serializer_class=AvailabilityUpdateSerializer)
    def availability(self, request, pk=None):
        game = self.get_object()
        if request.method == "GET":
            return Response(_availability_payload(game, request.user))

        serializer = AvailabilityUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if game.status != Game.Status.SCHEDULED:
            raise ValidationError({"detail": "Availability can only be changed for scheduled games."})

        roster = eligible_memberships(game.season)
        membership_id = serializer.validated_data.get("membership_id")
        if membership_id is None:
            # Prefer a season-specific roster spot over a team-wide one.
            membership = roster.filter(user=request.user).order_by(F("season_id").asc(nulls_last=True)).first()
            if membership is None:
                raise PermissionDenied("You don't have a roster spot for this game.")
        else:
            membership = roster.filter(pk=membership_id).first()
            if membership is None:
                raise ValidationError({"membership_id": "That player isn't on this game's roster."})
            if membership.user_id != request.user.id and not is_manager(request.user, game.team):
                raise PermissionDenied("You can only update your own availability.")

        GameAvailability.objects.update_or_create(
            game=game, membership=membership, defaults={"status": serializer.validated_data["status"]}
        )
        return Response(_availability_payload(game, request.user))

    @action(detail=True, methods=["get", "put"], serializer_class=LineupInputSerializer)
    def lineup(self, request, pk=None):
        game = self.get_object()
        if request.method == "GET":
            return Response(_lineup_payload(game))

        require_manager(request.user, game.team)
        if game.status == Game.Status.CANCELLED:
            raise ValidationError({"detail": "This game was cancelled."})
        serializer = LineupInputSerializer(data=request.data, context={"season": game.season})
        serializer.is_valid(raise_exception=True)
        players = serializer.validated_data["players"]

        with transaction.atomic():
            Game.objects.select_for_update().get(pk=game.pk)  # serialize concurrent saves
            lineup, _ = Lineup.objects.get_or_create(game=game)
            lineup.spots.all().delete()
            LineupSpot.objects.bulk_create(
                [
                    LineupSpot(
                        lineup=lineup,
                        membership_id=p["membership_id"],
                        batting_order=p["batting_order"],
                        defensive_position=p["defensive_position"],
                    )
                    for p in players
                ]
            )
            lineup.save(update_fields=["updated_at"])

        unavailable = (
            GameAvailability.objects.filter(
                game=game,
                membership_id__in=[p["membership_id"] for p in players],
                status=GameAvailability.Status.NO,
            )
            .select_related("membership")
            .order_by("membership__display_name")
        )
        warnings = [f"{record.membership.display_name} said they can't make this game." for record in unavailable]
        return Response(_lineup_payload(game, warnings))

    @action(detail=True, methods=["post"], serializer_class=GameResultSerializer)
    def result(self, request, pk=None):
        game = self.get_object()
        require_manager(request.user, game.team)
        serializer = GameResultSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        game.team_score = serializer.validated_data["team_score"]
        game.opponent_score = serializer.validated_data["opponent_score"]
        game.status = Game.Status.COMPLETED
        game.save(update_fields=["team_score", "opponent_score", "status", "updated_at"])
        return Response(GameSerializer(game, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["get", "put"], serializer_class=GameStatsInputSerializer)
    def stats(self, request, pk=None):
        game = self.get_object()
        if request.method == "GET":
            return Response(_game_stats_payload(game))

        require_manager(request.user, game.team)
        if game.status == Game.Status.CANCELLED:
            raise ValidationError({"detail": "This game was cancelled."})
        serializer = GameStatsInputSerializer(data=request.data, context={"season": game.season})
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            Game.objects.select_for_update().get(pk=game.pk)
            PlayerGameStats.objects.filter(game=game).delete()
            PlayerGameStats.objects.bulk_create(
                [
                    PlayerGameStats(
                        game=game,
                        membership_id=p["membership_id"],
                        **{field: p[field] for field in stats_service.COUNTING_FIELDS},
                    )
                    for p in serializer.validated_data["players"]
                ]
            )
        return Response(_game_stats_payload(game))
