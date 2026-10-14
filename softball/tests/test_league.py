from datetime import datetime
from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase
from django.utils import timezone

from softball import teamsideline
from softball.models import Game, PlayerGameStats

from .base import TeamFixture

FIXTURE = (Path(__file__).parent / "fixtures" / "teamsideline_print_view.html").read_text()
URL = "https://teamsideline.com/common/StandingsResultsPrintView.aspx?d=abc123"


class ParserTests(SimpleTestCase):
    def setUp(self):
        self.page = teamsideline.parse(FIXTURE)

    def test_league_name_and_teams(self):
        self.assertEqual(self.page.name, "Fall Softball League 2026 | Monday Mens DbHd Rec 2")
        self.assertEqual(
            self.page.teams,
            ["Local Boys", "Bat Intentions", "Back Door Sliders", "Moon Crew", "Ump Yours", "Talacheros"],
        )

    def test_standings_leave_out_coach_contact_details(self):
        first = self.page.standings[0]
        self.assertEqual(
            first,
            {
                "place": "1",
                "team": "Local Boys",
                "wins": "8",
                "losses": "2",
                "games_back": "--",
                "games_played": "10",
                "pct": "0.800",
                "streak": "Won 3",
            },
        )

    def test_games_skip_byes_rain_outs_and_holidays(self):
        regular = [g for g in self.page.games if g.section == "regular"]
        self.assertEqual(len(regular), 30)
        self.assertTrue(all(g.home and g.away for g in regular))
        opener = regular[0]
        self.assertEqual(opener.starts_at, datetime(2026, 8, 24, 18, 15))
        self.assertEqual((opener.home, opener.home_score, opener.away, opener.away_score), ("Local Boys", 11, "Moon Crew", 10))
        self.assertEqual(opener.location, "RRPSB1")
        self.assertEqual(opener.group, "Week 1")

    def test_playoffs_keep_their_game_numbers(self):
        playoff = {g.key: g for g in self.page.games if g.section == "playoff"}
        self.assertEqual(sorted(playoff), ["playoff:G1", "playoff:G2", "playoff:G3", "playoff:G4", "playoff:G5"])
        g3 = playoff["playoff:G3"]
        self.assertEqual((g3.home, g3.away, g3.home_score), ("Local Boys", "G1 Winner", None))
        self.assertEqual(g3.starts_at, datetime(2026, 10, 19, 19, 15))

    def test_not_a_league_page(self):
        with self.assertRaises(teamsideline.LeaguePageError):
            teamsideline.parse("<html><body>Hello</body></html>")

    def test_only_teamsideline_https_links(self):
        self.assertEqual(teamsideline.validate_url(f"  {URL} "), URL)
        teamsideline.validate_url("https://www.teamsideline.com/x")
        for bad in (
            "http://teamsideline.com/x",
            "https://teamsideline.com.example.org/x",
            "https://notteamsideline.com/x",
            "https://user:pw@teamsideline.com/x",
            "https://teamsideline.com:8080/x",
            "file:///etc/passwd",
        ):
            with self.subTest(bad=bad), self.assertRaises(teamsideline.LeaguePageError):
                teamsideline.validate_url(bad)


@mock.patch("softball.teamsideline.fetch", return_value=FIXTURE)
class LeagueSyncTests(TeamFixture):
    """The fixture team is "Bat Intentions", which is in the sample league."""

    def setUp(self):
        super().setUp()
        cache.clear()  # throttle counters

    def league_url(self, *suffix):
        return "/".join([f"/api/seasons/{self.season.id}/league", *suffix]) + "/"

    def link(self, team_name="Bat Intentions"):
        self.login(self.manager)
        return self.client.put(self.league_url(), {"url": URL, "team_name": team_name})

    def league_games(self):
        return self.season.games.exclude(league_key="")

    def test_preview_lists_the_teams(self, fetch):
        self.login(self.manager)
        response = self.client.post(self.league_url("preview"), {"url": URL})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["league_name"], "Fall Softball League 2026 | Monday Mens DbHd Rec 2")
        self.assertIn("Bat Intentions", response.data["teams"])
        fetch.assert_called_once_with(URL)

    def test_link_imports_the_teams_games_and_scores(self, fetch):
        response = self.link("bat  intentions")  # matching ignores case and spacing
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["team_name"], "Bat Intentions")
        self.assertEqual(response.data["sync"]["created"], 11)
        self.assertEqual(response.data["sync"]["scored"], 10)

        games = self.league_games()
        self.assertEqual(games.count(), 11)
        results = [g.result for g in games if g.status == Game.Status.COMPLETED]
        self.assertEqual((results.count("W"), results.count("L")), (8, 2))  # matches the standings

        opener = games.get(league_key="regular:2026-08-24T20:15")
        self.assertEqual((opener.opponent_name, opener.team_score, opener.opponent_score), ("Talacheros", 20, 5))
        self.assertEqual(opener.location, "RRPSB1")
        self.assertEqual(timezone.localtime(opener.scheduled_at).hour, 20)

        playoff = games.get(league_key="playoff:G4")
        self.assertEqual((playoff.opponent_name, playoff.status), ("G2 Winner", Game.Status.SCHEDULED))

        us = [row for row in response.data["standings"] if row["is_us"]]
        self.assertEqual([row["team"] for row in us], ["Bat Intentions"])
        self.assertNotIn("coach", response.data["standings"][0])

        # The game added by hand (not on the league page) is left alone.
        self.game.refresh_from_db()
        self.assertEqual((self.game.status, self.game.league_key), (Game.Status.SCHEDULED, ""))

    def test_unknown_team_lists_the_choices(self, fetch):
        response = self.link("Hit Happens")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Bat Intentions", response.data["teams"])
        self.season.refresh_from_db()
        self.assertEqual(self.season.league_url, "")

    def test_syncing_again_changes_nothing(self, fetch):
        self.link()
        response = self.client.post(self.league_url("sync"))
        self.assertEqual(response.status_code, 200, response.data)
        sync = response.data["sync"]
        self.assertEqual((sync["created"], sync["updated"], sync["scored"], sync["cancelled"]), (0, 0, 0, 0))
        self.assertEqual(sync["unchanged"], 11)
        self.assertEqual(self.league_games().count(), 11)

    def test_a_game_added_by_hand_at_the_same_time_is_adopted(self, fetch):
        manual = self.new_game(
            opponent_name="talacheros", scheduled_at=timezone.make_aware(datetime(2026, 8, 24, 20, 15))
        )
        self.link()
        manual.refresh_from_db()
        self.assertEqual((manual.league_key, manual.opponent_name), ("regular:2026-08-24T20:15", "Talacheros"))
        self.assertEqual(self.league_games().count(), 11)

    def test_scores_entered_in_the_app_win(self, fetch):
        self.link()
        game = self.league_games().get(league_key="regular:2026-08-24T20:15")
        game.team_score, game.opponent_score = 19, 5
        game.save()

        response = self.client.post(self.league_url("sync"))
        game.refresh_from_db()
        self.assertEqual((game.team_score, game.opponent_score), (19, 5))
        self.assertTrue(any("20-5" in note for note in response.data["sync"]["notes"]))

    def test_resolved_playoff_opponent_updates_the_same_game(self, fetch):
        self.link()
        playoff = self.league_games().get(league_key="playoff:G4")
        fetch.return_value = FIXTURE.replace(">G2 Winner<", ">Back Door Sliders<")

        response = self.client.post(self.league_url("sync"))
        self.assertEqual(response.data["sync"]["updated"], 1)
        playoff.refresh_from_db()
        self.assertEqual(playoff.opponent_name, "Back Door Sliders")

    def test_game_dropped_from_the_league_page_is_cancelled(self, fetch):
        self.link()
        playoff = self.league_games().get(league_key="playoff:G4")
        # G4 now lists someone else, so it isn't our game any more.
        fetch.return_value = FIXTURE.replace(
            'PlayoffGrid_ctl00_ctl15_HomeLabel">Bat Intentions<', 'PlayoffGrid_ctl00_ctl15_HomeLabel">Moon Crew<'
        )
        response = self.client.post(self.league_url("sync"))
        self.assertEqual(response.data["sync"]["cancelled"], 1)
        playoff.refresh_from_db()
        self.assertEqual(playoff.status, Game.Status.CANCELLED)

    def test_dropped_game_with_stats_is_left_alone(self, fetch):
        self.link()
        playoff = self.league_games().get(league_key="playoff:G4")
        PlayerGameStats.objects.create(game=playoff, membership=self.player_m, at_bats=2, hits=1, singles=1)
        fetch.return_value = FIXTURE.replace(
            'PlayoffGrid_ctl00_ctl15_HomeLabel">Bat Intentions<', 'PlayoffGrid_ctl00_ctl15_HomeLabel">Moon Crew<'
        )
        self.client.post(self.league_url("sync"))
        playoff.refresh_from_db()
        self.assertEqual(playoff.status, Game.Status.SCHEDULED)

    def test_unlink_keeps_the_games(self, fetch):
        self.link()
        response = self.client.delete(self.league_url())
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["linked"])
        self.assertEqual(self.league_games().count(), 11)

    def test_everyone_on_the_team_can_see_standings(self, fetch):
        self.link()
        self.login(self.player_user)
        response = self.client.get(self.league_url())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["linked"])
        self.assertEqual(len(response.data["standings"]), 6)

    def test_players_cant_link_or_sync(self, fetch):
        self.login(self.player_user)
        self.assertEqual(self.client.put(self.league_url(), {"url": URL, "team_name": "Bat Intentions"}).status_code, 403)
        self.assertEqual(self.client.post(self.league_url("sync")).status_code, 403)
        self.assertEqual(self.client.post(self.league_url("preview"), {"url": URL}).status_code, 403)
        fetch.assert_not_called()

    def test_other_teams_season_is_hidden(self, fetch):
        self.login(self.outsider)
        self.assertEqual(self.client.get(self.league_url()).status_code, 404)

    def test_rejects_other_sites(self, fetch):
        self.login(self.manager)
        response = self.client.post(self.league_url("preview"), {"url": "https://example.com/schedule"})
        self.assertEqual(response.status_code, 400)
        fetch.assert_not_called()

    def test_sync_needs_a_link(self, fetch):
        self.login(self.manager)
        self.assertEqual(self.client.post(self.league_url("sync")).status_code, 400)

    def test_league_site_down(self, fetch):
        fetch.side_effect = teamsideline.LeagueFetchError("Couldn't reach TeamSideline. Try again in a minute.")
        response = self.link()
        self.assertEqual(response.status_code, 502)

    def test_season_reports_link(self, fetch):
        self.link()
        response = self.client.get(f"/api/seasons/{self.season.id}/")
        self.assertTrue(response.data["league_linked"])
        self.assertIsNotNone(response.data["league_synced_at"])
