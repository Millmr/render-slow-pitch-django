"""Create a demo team with a roster, schedule, lineup and stats.

    python manage.py seed_demo

Log in as ``manager`` / ``softball-demo-1`` (or ``player1`` … ``player9``).
Refuses to run unless DJANGO_DEBUG is on, or you pass --force.
"""

import random
from datetime import date, timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from softball.models import (
    Game,
    GameAvailability,
    Lineup,
    LineupSpot,
    PlayerGameStats,
    Season,
    Team,
    TeamMembership,
)

PASSWORD = "softball-demo-1"
PLAYERS = [
    "Casey Jones", "Sam Rivera", "Jordan Lee", "Taylor Brooks", "Morgan Diaz", "Riley Chen",
    "Avery Patel", "Quinn Harper", "Jamie Ortiz", "Drew Walsh", "Skyler Kim", "Reese Moore",
]
POSITIONS = ["P", "C", "1B", "2B", "3B", "SS", "LF", "LCF", "RCF", "RF", "EH", "EH"]
OPPONENTS = ["Hit Happens", "Big Dill Pickles", "Pitch Perfect", "Can't Touch This", "Diamond Dogs"]


class Command(BaseCommand):
    help = "Create a demo team with sample data."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Run even when DEBUG is off.")

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError("Refusing to seed demo data with DEBUG off. Pass --force if you mean it.")

        User = get_user_model()
        rng = random.Random(7)

        manager, _ = User.objects.get_or_create(
            username="manager", defaults={"first_name": "Mia", "last_name": "Manager"}
        )
        manager.set_password(PASSWORD)
        manager.save()

        team = Team.objects.create(name="The Bat Intentions", created_by=manager)
        season = Season.objects.create(
            team=team, name=f"Fall {date.today().year}", start_date=date.today() - timedelta(days=30)
        )
        TeamMembership.objects.create(
            team=team, user=manager, display_name="Mia Manager", role=TeamMembership.Role.MANAGER, jersey_number=1
        )

        roster = []
        for i, name in enumerate(PLAYERS, start=1):
            user = None
            if i <= 9:  # the last few stay unregistered, like real rosters
                first, last = name.split()
                user, _ = User.objects.get_or_create(
                    username=f"player{i}", defaults={"first_name": first, "last_name": last}
                )
                user.set_password(PASSWORD)
                user.save()
            roster.append(
                TeamMembership.objects.create(
                    team=team,
                    season=season,
                    user=user,
                    display_name=name,
                    jersey_number=i + 1,
                    is_substitute=(i == len(PLAYERS)),
                )
            )

        now = timezone.now()
        for offset, opponent in zip([-21, -14, -7, 3, 10], OPPONENTS):
            game = Game.objects.create(
                season=season, opponent_name=opponent, scheduled_at=now + timedelta(days=offset), location="Field 3"
            )
            if offset < 0:
                game.team_score, game.opponent_score = rng.randint(6, 22), rng.randint(6, 22)
                game.status = Game.Status.COMPLETED
                game.save()
                for member in roster[:11]:
                    at_bats = [rng.choice(["1B", "1B", "1B", "2B", "3B", "HR", None, None]) for _ in range(rng.randint(3, 4))]
                    PlayerGameStats.objects.create(
                        game=game,
                        membership=member,
                        at_bats=len(at_bats),
                        singles=at_bats.count("1B"),
                        doubles=at_bats.count("2B"),
                        triples=at_bats.count("3B"),
                        home_runs=at_bats.count("HR"),
                        hits=sum(result is not None for result in at_bats),
                        walks=rng.randint(0, 1),
                    )
            else:
                for member in roster[:9]:
                    GameAvailability.objects.create(
                        game=game, membership=member, status=rng.choice(["yes", "yes", "no", "unsure"])
                    )

        next_game = season.games.filter(status=Game.Status.SCHEDULED).first()
        lineup = Lineup.objects.create(game=next_game)
        for order, (member, position) in enumerate(zip(roster, POSITIONS), start=1):
            LineupSpot.objects.create(lineup=lineup, membership=member, batting_order=order, defensive_position=position)

        self.stdout.write(
            self.style.SUCCESS(
                f"Created '{team}' (team {team.id}, season {season.id}). Log in as manager / {PASSWORD}."
            )
        )
