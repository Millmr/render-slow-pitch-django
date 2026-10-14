from rest_framework import status

from softball import stats
from softball.models import Lineup, LineupSpot, PlateAppearance, PlayerGameStats, TeamMembership

from .base import TeamFixture, User


class BookTests(TeamFixture):
    def setUp(self):
        super().setUp()
        self.keeper_user = User.objects.create_user("keeper", password="Str0ng-pass!")
        self.keeper_m = TeamMembership.objects.create(
            team=self.team, season=self.season, user=self.keeper_user, display_name="Kim",
            role=TeamMembership.Role.SCOREKEEPER,
        )
        lineup = Lineup.objects.create(game=self.game)
        for order, (member, position) in enumerate(
            [(self.player_m, "SS"), (self.player2_m, "CF"), (self.unregistered_m, "P")], start=1
        ):
            LineupSpot.objects.create(lineup=lineup, membership=member, batting_order=order, defensive_position=position)

    def url(self, game=None):
        return f"/api/games/{(game or self.game).id}/book/"

    def record(self, result, **extra):
        return self.client.post(self.url(), {"result": result, **extra})

    # ---- permissions

    def test_scorekeeper_and_manager_can_record(self):
        self.login(self.keeper_user)
        response = self.record("1B")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertTrue(response.data["can_edit"])
        self.login(self.manager)
        self.assertEqual(self.record("OUT").status_code, status.HTTP_201_CREATED)

    def test_players_can_view_but_not_record(self):
        self.login(self.keeper_user)
        self.record("2B")
        self.login(self.player_user)
        view = self.client.get(self.url())
        self.assertEqual(view.status_code, status.HTTP_200_OK)
        self.assertFalse(view.data["can_edit"])
        self.assertEqual([pa["result"] for pa in view.data["plate_appearances"]], ["2B"])
        self.assertEqual(self.record("HR").status_code, status.HTTP_403_FORBIDDEN)
        pa_id = view.data["plate_appearances"][0]["id"]
        self.assertEqual(self.client.delete(f"{self.url()}{pa_id}/").status_code, status.HTTP_403_FORBIDDEN)

    def test_other_team_cannot_see_the_book(self):
        self.login(self.outsider)
        self.assertEqual(self.client.get(self.url()).status_code, status.HTTP_404_NOT_FOUND)

    def test_scorekeeper_is_not_a_manager(self):
        self.login(self.keeper_user)
        team = self.client.get(f"/api/teams/{self.team.id}/")
        self.assertEqual(team.data["my_role"], "scorekeeper")
        lineup = self.client.put(f"/api/games/{self.game.id}/lineup/", {"players": []})
        self.assertEqual(lineup.status_code, status.HTTP_403_FORBIDDEN)

    def test_manager_can_make_someone_scorekeeper(self):
        self.login(self.manager)
        response = self.client.patch(f"/api/memberships/{self.player2_m.id}/", {"role": "scorekeeper"})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.player2_m.refresh_from_db()
        self.assertEqual(self.player2_m.role, "scorekeeper")

    # ---- batting order and innings

    def test_next_batter_follows_the_lineup_and_wraps(self):
        self.login(self.keeper_user)
        book = self.client.get(self.url()).data
        self.assertEqual(book["next_batter"]["display_name"], "Pat")
        self.assertEqual(book["on_deck"]["display_name"], "Quinn")
        for result in ("1B", "OUT", "BB"):
            book = self.record(result).data
        self.assertEqual(
            [(pa["display_name"], pa["result"]) for pa in book["plate_appearances"]],
            [("Pat", "1B"), ("Quinn", "OUT"), ("Ursula", "BB")],
        )
        self.assertEqual(book["next_batter"]["display_name"], "Pat")  # back to the top

    def test_three_outs_start_the_next_inning_and_sac_flies_count_as_outs(self):
        self.login(self.keeper_user)
        self.record("OUT")
        book = self.record("SF").data
        self.assertEqual((book["inning"], book["outs"]), (1, 2))
        book = self.record("OUT").data
        self.assertEqual((book["inning"], book["outs"]), (2, 0))
        book = self.record("HR").data
        self.assertEqual(book["plate_appearances"][-1]["inning"], 2)

    def test_scorekeeper_can_end_an_inning_early(self):
        self.login(self.keeper_user)
        self.record("1B")
        book = self.record("2B", inning=2).data  # run-rule: inning ended with 0 outs
        self.assertEqual(book["plate_appearances"][-1]["inning"], 2)
        self.assertEqual(book["inning"], 2)

    def test_pinch_hitter_then_lineup_continues(self):
        self.login(self.keeper_user)
        self.record("1B")  # Pat
        self.record("OUT", membership_id=self.keeper_m.id)  # Kim bats in Quinn's spot
        book = self.client.get(self.url()).data
        # Continues after the last lineup batter (Pat), so Quinn is up.
        self.assertEqual(book["next_batter"]["display_name"], "Quinn")

    def test_no_lineup_requires_choosing_the_batter(self):
        game = self.new_game()
        self.login(self.keeper_user)
        response = self.client.post(self.url(game), {"result": "1B"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        response = self.client.post(self.url(game), {"result": "1B", "membership_id": self.player_m.id})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

    def test_cannot_record_for_another_teams_player(self):
        self.login(self.keeper_user)
        response = self.record("HR", membership_id=self.ringer_m.id)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cancelled_games_cannot_be_scored(self):
        self.game.status = "cancelled"
        self.game.save()
        self.login(self.keeper_user)
        self.assertEqual(self.record("1B").status_code, status.HTTP_400_BAD_REQUEST)

    # ---- stats from the book

    def test_book_builds_the_game_stats(self):
        self.login(self.keeper_user)
        # Pat: 1B, SF, HR   Quinn: OUT, BB   Ursula: 2B
        for result in ("1B", "OUT", "2B", "SF", "BB", "OUT", "HR"):
            self.record(result)
        pat = PlayerGameStats.objects.get(game=self.game, membership=self.player_m)
        self.assertEqual(
            (pat.at_bats, pat.hits, pat.singles, pat.home_runs, pat.sac_flies, pat.walks), (2, 2, 1, 1, 1, 0)
        )
        quinn = PlayerGameStats.objects.get(game=self.game, membership=self.player2_m)
        self.assertEqual((quinn.at_bats, quinn.hits, quinn.walks), (1, 0, 1))

        line = stats.with_rates(
            {"at_bats": 2, "hits": 2, "singles": 1, "doubles": 0, "triples": 0, "home_runs": 1, "walks": 0, "sac_flies": 1}
        )
        self.assertEqual(line["on_base_percentage"], round(2 / 3, 3))  # SF counts against OBP

    def test_leaderboard_reflects_the_book_once_the_game_is_final(self):
        self.login(self.keeper_user)
        self.record("HR")
        leaderboard = f"/api/seasons/{self.season.id}/leaderboard/"
        self.assertEqual(self.client.get(leaderboard).data["players"], [])  # not final yet
        self.complete(self.game)
        players = self.client.get(leaderboard).data["players"]
        self.assertEqual((players[0]["display_name"], players[0]["home_runs"]), ("Pat", 1))

    def test_undo_and_fix_plays(self):
        self.login(self.keeper_user)
        self.record("1B")
        book = self.record("OUT").data
        last = book["plate_appearances"][-1]["id"]

        book = self.client.delete(f"{self.url()}{last}/").data
        self.assertEqual(len(book["plate_appearances"]), 1)
        self.assertEqual(book["next_batter"]["display_name"], "Quinn")

        first = book["plate_appearances"][0]["id"]
        response = self.client.patch(f"{self.url()}{first}/", {"result": "3B"})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        pat = PlayerGameStats.objects.get(game=self.game, membership=self.player_m)
        self.assertEqual((pat.singles, pat.triples), (0, 1))

    def test_removing_every_play_clears_the_stats(self):
        self.login(self.keeper_user)
        pa_id = self.record("1B").data["plate_appearances"][0]["id"]
        self.client.delete(f"{self.url()}{pa_id}/")
        self.assertFalse(PlayerGameStats.objects.filter(game=self.game).exists())

    def test_cannot_touch_another_games_play(self):
        other = self.new_game()
        PlateAppearance.objects.create(game=other, membership=self.player_m, inning=1, sequence=1, result="1B")
        pa = other.plate_appearances.get()
        self.login(self.keeper_user)
        self.assertEqual(self.client.delete(f"{self.url()}{pa.id}/").status_code, status.HTTP_404_NOT_FOUND)

    def test_manual_stats_are_locked_once_the_book_is_used(self):
        self.login(self.keeper_user)
        self.record("1B")
        self.login(self.manager)
        stats_url = f"/api/games/{self.game.id}/stats/"
        self.assertTrue(self.client.get(stats_url).data["from_book"])
        response = self.client.put(stats_url, {"players": [{"membership_id": self.player_m.id, "at_bats": 4}]})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_manual_stats_accept_sac_flies(self):
        self.complete(self.game)
        self.login(self.manager)
        response = self.client.put(
            f"/api/games/{self.game.id}/stats/",
            {"players": [{"membership_id": self.player_m.id, "at_bats": 3, "singles": 1, "sac_flies": 1}]},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["players"][0]["sac_flies"], 1)
        self.assertFalse(response.data["from_book"])
