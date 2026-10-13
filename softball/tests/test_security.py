"""Cross-team isolation: changing an ID in the URL must never reach another team."""

from rest_framework import status

from .base import TeamFixture


class UnauthenticatedTests(TeamFixture):
    def test_private_endpoints_require_login(self):
        for url in (
            "/api/teams/",
            f"/api/teams/{self.team.id}/",
            f"/api/teams/{self.team.id}/roster/",
            f"/api/seasons/{self.season.id}/games/",
            f"/api/seasons/{self.season.id}/leaderboard/",
            f"/api/games/{self.game.id}/",
            f"/api/games/{self.game.id}/lineup/",
            f"/api/games/{self.game.id}/stats/",
            f"/api/memberships/{self.player_m.id}/",
        ):
            self.assertEqual(self.client.get(url).status_code, status.HTTP_401_UNAUTHORIZED, url)

    def test_health_check_is_public(self):
        self.assertEqual(self.client.get("/api/health/").status_code, status.HTTP_200_OK)


class CrossTeamTests(TeamFixture):
    """Our manager pokes at the other team's objects by ID."""

    def setUp(self):
        super().setUp()
        self.login(self.manager)

    def assert_not_found(self, method, url, data=None):
        response = getattr(self.client, method)(url, data or {})
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, f"{method.upper()} {url}")

    def test_cannot_read_other_team(self):
        for url in (
            f"/api/teams/{self.other_team.id}/",
            f"/api/teams/{self.other_team.id}/roster/",
            f"/api/teams/{self.other_team.id}/seasons/",
            f"/api/seasons/{self.other_season.id}/",
            f"/api/seasons/{self.other_season.id}/games/",
            f"/api/seasons/{self.other_season.id}/leaderboard/",
            f"/api/games/{self.other_game.id}/",
            f"/api/games/{self.other_game.id}/availability/",
            f"/api/games/{self.other_game.id}/lineup/",
            f"/api/games/{self.other_game.id}/stats/",
            f"/api/memberships/{self.ringer_m.id}/",
            f"/api/memberships/{self.ringer_m.id}/stats/",
        ):
            self.assert_not_found("get", url)

    def test_cannot_write_other_team(self):
        self.assert_not_found("patch", f"/api/teams/{self.other_team.id}/", {"name": "Ours now"})
        self.assert_not_found("delete", f"/api/teams/{self.other_team.id}/")
        self.assert_not_found("post", f"/api/teams/{self.other_team.id}/roster/", {"display_name": "Spy"})
        self.assert_not_found("patch", f"/api/memberships/{self.ringer_m.id}/", {"is_active": False})
        self.assert_not_found(
            "post",
            f"/api/seasons/{self.other_season.id}/games/",
            {"opponent_name": "X", "scheduled_at": "2026-10-20T18:00:00Z"},
        )
        self.assert_not_found("patch", f"/api/games/{self.other_game.id}/", {"location": "Nowhere"})
        self.assert_not_found("post", f"/api/games/{self.other_game.id}/result/", {"team_score": 1, "opponent_score": 0})
        self.assert_not_found("put", f"/api/games/{self.other_game.id}/lineup/", {"players": []})
        self.assert_not_found("put", f"/api/games/{self.other_game.id}/stats/", {"players": []})
        self.assert_not_found("patch", f"/api/games/{self.other_game.id}/availability/", {"status": "yes"})

        self.other_game.refresh_from_db()
        self.assertEqual(self.other_game.location, "")
        self.assertEqual(self.other_game.status, "scheduled")

    def test_season_filter_on_roster_cannot_use_other_teams_season(self):
        self.assert_not_found("get", f"/api/teams/{self.team.id}/roster/?season={self.other_season.id}")
