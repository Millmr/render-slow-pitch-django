from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from softball.models import Game, PlayerGameStats, Season, Team, TeamMembership

User = get_user_model()


class TeamFixture(APITestCase):
    """Two teams: ours (manager + players) and another team we must never touch."""

    def setUp(self):
        self.manager = User.objects.create_user("manager", password="Str0ng-pass!")
        self.player_user = User.objects.create_user("player", password="Str0ng-pass!")
        self.other_player_user = User.objects.create_user("player2", password="Str0ng-pass!")
        self.outsider = User.objects.create_user("outsider", password="Str0ng-pass!")

        self.team = Team.objects.create(name="Bat Intentions", created_by=self.manager)
        self.season = Season.objects.create(team=self.team, name="Fall 2026", start_date=date(2026, 9, 1))
        self.manager_m = TeamMembership.objects.create(
            team=self.team, user=self.manager, display_name="Mia", role=TeamMembership.Role.MANAGER
        )
        self.player_m = TeamMembership.objects.create(
            team=self.team, season=self.season, user=self.player_user, display_name="Pat", jersey_number=7
        )
        self.player2_m = TeamMembership.objects.create(
            team=self.team, season=self.season, user=self.other_player_user, display_name="Quinn"
        )
        self.unregistered_m = TeamMembership.objects.create(
            team=self.team, season=self.season, display_name="Ursula"
        )
        self.game = Game.objects.create(
            season=self.season, opponent_name="Hit Happens", scheduled_at=timezone.now() + timedelta(days=3)
        )

        self.other_team = Team.objects.create(name="Other Team", created_by=self.outsider)
        self.other_season = Season.objects.create(team=self.other_team, name="Fall 2026", start_date=date(2026, 9, 1))
        self.outsider_m = TeamMembership.objects.create(
            team=self.other_team, user=self.outsider, display_name="Olive", role=TeamMembership.Role.MANAGER
        )
        self.ringer_m = TeamMembership.objects.create(
            team=self.other_team, season=self.other_season, display_name="Ringer"
        )
        self.other_game = Game.objects.create(
            season=self.other_season, opponent_name="Somebody", scheduled_at=timezone.now() + timedelta(days=2)
        )

    def login(self, user):
        self.client.force_authenticate(user=user)

    def complete(self, game, team_score=10, opponent_score=5):
        game.status = Game.Status.COMPLETED
        game.team_score = team_score
        game.opponent_score = opponent_score
        game.save()
        return game

    def add_stats(self, game, membership, **counts):
        counts.setdefault("hits", sum(counts.get(f, 0) for f in ("singles", "doubles", "triples", "home_runs")))
        return PlayerGameStats.objects.create(game=game, membership=membership, **counts)

    def new_game(self, **kwargs):
        kwargs.setdefault("season", self.season)
        kwargs.setdefault("opponent_name", "Pitch Perfect")
        kwargs.setdefault("scheduled_at", timezone.now() + timedelta(days=7))
        return Game.objects.create(**kwargs)
