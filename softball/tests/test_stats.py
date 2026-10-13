from django.db import IntegrityError, transaction
from rest_framework import status

from softball import stats
from softball.models import Game, PlayerGameStats

from .base import TeamFixture


class GameStatsEntryTests(TeamFixture):
    def url(self, game=None):
        return f"/api/games/{(game or self.game).id}/stats/"

    def line(self, membership, **counts):
        return {"membership_id": membership.id, **counts}

    def test_manager_enters_stats_and_hits_are_computed(self):
        self.complete(self.game)
        self.login(self.manager)
        response = self.client.put(
            self.url(),
            {"players": [self.line(self.player_m, at_bats=4, singles=1, doubles=1, home_runs=1, walks=1)]},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        row = response.data["players"][0]
        self.assertEqual(row["hits"], 3)
        self.assertEqual(row["batting_average"], 0.75)
        self.assertEqual(row["batting_average_display"], ".750")
        self.assertTrue(response.data["counts_toward_season"])

    def test_hits_must_match_breakdown(self):
        self.login(self.manager)
        response = self.client.put(self.url(), {"players": [self.line(self.player_m, at_bats=4, hits=3, singles=1)]})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_hits_cannot_exceed_at_bats(self):
        self.login(self.manager)
        response = self.client.put(self.url(), {"players": [self.line(self.player_m, at_bats=1, singles=2)]})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_duplicate_player_lines_rejected(self):
        self.login(self.manager)
        response = self.client.put(
            self.url(),
            {"players": [self.line(self.player_m, at_bats=3), self.line(self.player_m, at_bats=2)]},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_database_also_rejects_duplicate_or_impossible_lines(self):
        self.add_stats(self.game, self.player_m, at_bats=3, singles=1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.add_stats(self.game, self.player_m, at_bats=3)
        with self.assertRaises(IntegrityError), transaction.atomic():
            PlayerGameStats.objects.create(game=self.game, membership=self.player2_m, at_bats=1, hits=2, singles=2)

    def test_other_teams_player_rejected(self):
        self.login(self.manager)
        response = self.client.put(self.url(), {"players": [self.line(self.ringer_m, at_bats=3)]})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_player_cannot_enter_stats(self):
        self.login(self.player_user)
        response = self.client.put(self.url(), {"players": [self.line(self.player_m, at_bats=4, home_runs=4)]})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_inactive_player_keeps_stats_through_corrections(self):
        self.complete(self.game)
        self.add_stats(self.game, self.player2_m, at_bats=3, singles=1)
        self.player2_m.is_active = False
        self.player2_m.save()
        self.login(self.manager)
        response = self.client.put(
            self.url(),
            {"players": [self.line(self.player2_m, at_bats=3, singles=2), self.line(self.player_m, at_bats=2)]},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)


class LeaderboardTests(TeamFixture):
    def url(self, sort=None):
        base = f"/api/seasons/{self.season.id}/leaderboard/"
        return f"{base}?sort={sort}" if sort else base

    def test_only_completed_games_count(self):
        completed = self.complete(self.game)
        scheduled = self.new_game()
        cancelled = self.new_game(status=Game.Status.CANCELLED)
        self.add_stats(completed, self.player_m, at_bats=4, singles=2)
        self.add_stats(scheduled, self.player_m, at_bats=4, home_runs=4)
        self.add_stats(cancelled, self.player_m, at_bats=4, home_runs=4)

        self.login(self.player_user)
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        pat = response.data["players"][0]
        self.assertEqual((pat["games_played"], pat["at_bats"], pat["hits"], pat["home_runs"]), (1, 4, 2, 0))
        self.assertEqual(pat["batting_average"], 0.5)

    def test_season_totals_and_rates(self):
        second = self.complete(self.new_game())
        self.complete(self.game)
        self.add_stats(self.game, self.player_m, at_bats=4, singles=1, doubles=1, walks=1)
        self.add_stats(second, self.player_m, at_bats=3, home_runs=1, walks=0)

        line = stats.player_season_line(self.player_m, self.season)
        self.assertEqual(line["games_played"], 2)
        self.assertEqual(line["at_bats"], 7)
        self.assertEqual(line["hits"], 3)
        self.assertEqual(line["batting_average"], round(3 / 7, 3))
        self.assertEqual(line["on_base_percentage"], round(4 / 8, 3))
        self.assertEqual(line["slugging_percentage"], 1.0)
        self.assertEqual(line["slugging_percentage_display"], "1.000")

    def test_zero_at_bats_shows_dash_and_sorts_last(self):
        self.complete(self.game)
        self.add_stats(self.game, self.player_m, at_bats=0, walks=2)
        self.add_stats(self.game, self.player2_m, at_bats=4, singles=1)
        self.login(self.manager)
        players = self.client.get(self.url()).data["players"]
        self.assertEqual([p["display_name"] for p in players], ["Quinn", "Pat"])
        self.assertIsNone(players[1]["batting_average"])
        self.assertEqual(players[1]["batting_average_display"], "—")
        self.assertEqual(players[1]["rank"], 2)

    def test_sorting_by_home_runs(self):
        self.complete(self.game)
        self.add_stats(self.game, self.player_m, at_bats=4, singles=4)
        self.add_stats(self.game, self.player2_m, at_bats=4, home_runs=1)
        self.login(self.manager)
        players = self.client.get(self.url("home_runs")).data["players"]
        self.assertEqual(players[0]["display_name"], "Quinn")
        players = self.client.get(self.url("batting_average")).data["players"]
        self.assertEqual(players[0]["display_name"], "Pat")

    def test_unknown_sort_field_rejected(self):
        self.login(self.manager)
        for bad in ("password", "membership__user__password", "-hits"):
            response = self.client.get(self.url(bad))
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, bad)

    def test_editing_stats_updates_leaderboard(self):
        self.complete(self.game)
        self.login(self.manager)
        stats_url = f"/api/games/{self.game.id}/stats/"
        self.client.put(stats_url, {"players": [{"membership_id": self.player_m.id, "at_bats": 4, "singles": 1}]})
        self.assertEqual(self.client.get(self.url()).data["players"][0]["hits"], 1)

        self.client.put(stats_url, {"players": [{"membership_id": self.player_m.id, "at_bats": 4, "singles": 3}]})
        self.assertEqual(self.client.get(self.url()).data["players"][0]["hits"], 3)
        self.assertEqual(PlayerGameStats.objects.filter(game=self.game).count(), 1)

    def test_cancelling_a_game_removes_it_from_leaderboard(self):
        self.complete(self.game)
        self.add_stats(self.game, self.player_m, at_bats=4, singles=4)
        self.login(self.manager)
        self.assertEqual(len(self.client.get(self.url()).data["players"]), 1)
        self.client.patch(f"/api/games/{self.game.id}/", {"status": "cancelled"})
        self.assertEqual(self.client.get(self.url()).data["players"], [])

    def test_player_profile_matches_leaderboard(self):
        self.complete(self.game)
        self.add_stats(self.game, self.player_m, at_bats=3, singles=1, triples=1)
        self.login(self.player_user)
        profile = self.client.get(f"/api/memberships/{self.player_m.id}/stats/")
        self.assertEqual(profile.status_code, status.HTTP_200_OK)
        board = self.client.get(self.url()).data["players"][0]
        for field in ("hits", "at_bats", "batting_average", "on_base_percentage", "games_played"):
            self.assertEqual(profile.data["totals"][field], board[field], field)
        self.assertEqual(len(profile.data["games"]), 1)

    def test_rate_formatting(self):
        self.assertEqual(stats.format_rate(None), "—")
        self.assertEqual(stats.format_rate(0.3333), ".333")
        self.assertEqual(stats.format_rate(0.0), ".000")
        self.assertEqual(stats.format_rate(1.0), "1.000")
