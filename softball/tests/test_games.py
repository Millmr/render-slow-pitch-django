from rest_framework import status

from softball.models import Game, GameAvailability

from .base import TeamFixture


class ScheduleTests(TeamFixture):
    def test_manager_schedules_game(self):
        self.login(self.manager)
        response = self.client.post(
            f"/api/seasons/{self.season.id}/games/",
            {"opponent_name": "Big Dill Pickles", "scheduled_at": "2026-10-20T18:30:00-05:00", "location": "Field 2"},
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["status"], "scheduled")
        self.assertIsNone(response.data["result"])

    def test_player_cannot_schedule_or_edit_games(self):
        self.login(self.player_user)
        create = self.client.post(
            f"/api/seasons/{self.season.id}/games/",
            {"opponent_name": "Nope", "scheduled_at": "2026-10-20T18:30:00-05:00"},
        )
        self.assertEqual(create.status_code, status.HTTP_403_FORBIDDEN)
        edit = self.client.patch(f"/api/games/{self.game.id}/", {"location": "My backyard"})
        self.assertEqual(edit.status_code, status.HTTP_403_FORBIDDEN)

    def test_schedule_lists_games_and_filters_by_status(self):
        cancelled = self.new_game(status=Game.Status.CANCELLED)
        self.login(self.player_user)
        everything = self.client.get(f"/api/seasons/{self.season.id}/games/")
        self.assertEqual({g["id"] for g in everything.data}, {self.game.id, cancelled.id})
        scheduled = self.client.get(f"/api/seasons/{self.season.id}/games/?status=scheduled")
        self.assertEqual([g["id"] for g in scheduled.data], [self.game.id])
        bad = self.client.get(f"/api/seasons/{self.season.id}/games/?status=postponed")
        self.assertEqual(bad.status_code, status.HTTP_400_BAD_REQUEST)

    def test_game_cannot_be_completed_by_patch(self):
        self.login(self.manager)
        response = self.client.patch(f"/api/games/{self.game.id}/", {"status": "completed"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_put_on_game_detail_is_not_allowed(self):
        self.login(self.manager)
        response = self.client.put(
            f"/api/games/{self.game.id}/", {"opponent_name": "X", "scheduled_at": "2026-10-20T18:30:00-05:00"}
        )
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)


class ResultTests(TeamFixture):
    def test_manager_records_result_and_outcome_is_derived(self):
        self.login(self.manager)
        response = self.client.post(f"/api/games/{self.game.id}/result/", {"team_score": 12, "opponent_score": 9})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["status"], "completed")
        self.assertEqual(response.data["result"], "W")

        # Correcting the score updates the outcome too.
        response = self.client.post(f"/api/games/{self.game.id}/result/", {"team_score": 9, "opponent_score": 9})
        self.assertEqual(response.data["result"], "T")

    def test_player_cannot_submit_result(self):
        self.login(self.player_user)
        response = self.client.post(f"/api/games/{self.game.id}/result/", {"team_score": 30, "opponent_score": 0})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.game.refresh_from_db()
        self.assertEqual(self.game.status, Game.Status.SCHEDULED)

    def test_negative_scores_rejected(self):
        self.login(self.manager)
        response = self.client.post(f"/api/games/{self.game.id}/result/", {"team_score": -1, "opponent_score": 3})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cancelling_a_completed_game_clears_the_score(self):
        self.complete(self.game)
        self.login(self.manager)
        response = self.client.patch(f"/api/games/{self.game.id}/", {"status": "cancelled"})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertIsNone(response.data["team_score"])
        self.assertIsNone(response.data["result"])


class AvailabilityTests(TeamFixture):
    def url(self, game=None):
        return f"/api/games/{(game or self.game).id}/availability/"

    def test_player_responds_and_summary_updates(self):
        self.login(self.player_user)
        response = self.client.patch(self.url(), {"status": "yes"})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["summary"]["yes"], 1)
        self.assertEqual(response.data["my_membership_id"], self.player_m.id)

        missing = {m["display_name"] for m in response.data["missing"]}
        self.assertEqual(missing, {"Mia", "Quinn", "Ursula"})
        self.assertEqual(response.data["summary"]["no_response"], 3)

        # Changing your mind updates the same record.
        self.client.patch(self.url(), {"status": "no"})
        self.assertEqual(GameAvailability.objects.filter(game=self.game).count(), 1)
        self.assertEqual(GameAvailability.objects.get(game=self.game).status, "no")

    def test_game_detail_shows_my_availability(self):
        GameAvailability.objects.create(game=self.game, membership=self.player_m, status="unsure")
        self.login(self.player_user)
        self.assertEqual(self.client.get(f"/api/games/{self.game.id}/").data["my_availability"], "unsure")

    def test_player_cannot_answer_for_someone_else(self):
        self.login(self.player_user)
        response = self.client.patch(self.url(), {"status": "no", "membership_id": self.player2_m.id})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_manager_can_answer_for_unregistered_player(self):
        self.login(self.manager)
        response = self.client.patch(self.url(), {"status": "yes", "membership_id": self.unregistered_m.id})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

    def test_substitutes_are_not_counted_as_missing_until_they_answer(self):
        self.unregistered_m.is_substitute = True
        self.unregistered_m.save()
        self.login(self.manager)
        response = self.client.get(self.url())
        self.assertNotIn("Ursula", {m["display_name"] for m in response.data["missing"]})

        response = self.client.patch(self.url(), {"status": "yes", "membership_id": self.unregistered_m.id})
        sub = next(r for r in response.data["responses"] if r["display_name"] == "Ursula")
        self.assertTrue(sub["is_substitute"])

    def test_manager_cannot_set_availability_for_another_teams_player(self):
        self.login(self.manager)
        response = self.client.patch(self.url(), {"status": "yes", "membership_id": self.ringer_m.id})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_availability_is_locked_once_game_is_not_scheduled(self):
        self.complete(self.game)
        self.login(self.player_user)
        response = self.client.patch(self.url(), {"status": "yes"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
