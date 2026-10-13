from rest_framework import status

from softball.models import Game, GameAvailability, LineupSpot

from .base import TeamFixture


class LineupTests(TeamFixture):
    def url(self):
        return f"/api/games/{self.game.id}/lineup/"

    def spot(self, membership, order, position):
        return {"membership_id": membership.id, "batting_order": order, "defensive_position": position}

    def test_manager_saves_and_reopens_lineup(self):
        self.login(self.manager)
        payload = {
            "players": [
                self.spot(self.player_m, 1, "SS"),
                self.spot(self.manager_m, 2, "P"),
                self.spot(self.unregistered_m, 3, "EH"),
            ]
        }
        response = self.client.put(self.url(), payload)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        self.login(self.player_user)
        reopened = self.client.get(self.url())
        self.assertEqual(
            [(p["display_name"], p["batting_order"], p["defensive_position"]) for p in reopened.data["players"]],
            [("Pat", 1, "SS"), ("Mia", 2, "P"), ("Ursula", 3, "EH")],
        )
        self.assertIsNotNone(reopened.data["updated_at"])

    def test_saving_replaces_the_previous_lineup(self):
        self.login(self.manager)
        self.client.put(self.url(), {"players": [self.spot(self.player_m, 1, "SS"), self.spot(self.player2_m, 2, "CF")]})
        self.client.put(self.url(), {"players": [self.spot(self.player2_m, 1, "SS")]})
        self.assertEqual(LineupSpot.objects.filter(lineup__game=self.game).count(), 1)

    def test_empty_lineup_before_one_is_saved(self):
        self.login(self.player_user)
        response = self.client.get(self.url())
        self.assertEqual(response.data["players"], [])
        self.assertIsNone(response.data["updated_at"])

    def test_duplicate_batting_slot_rejected(self):
        self.login(self.manager)
        response = self.client.put(
            self.url(), {"players": [self.spot(self.player_m, 1, "SS"), self.spot(self.player2_m, 1, "CF")]}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_gap_in_batting_order_rejected(self):
        self.login(self.manager)
        response = self.client.put(
            self.url(), {"players": [self.spot(self.player_m, 1, "SS"), self.spot(self.player2_m, 3, "CF")]}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_player_listed_twice_rejected(self):
        self.login(self.manager)
        response = self.client.put(
            self.url(), {"players": [self.spot(self.player_m, 1, "SS"), self.spot(self.player_m, 2, "CF")]}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_duplicate_defensive_position_rejected_except_extra_hitter(self):
        self.login(self.manager)
        two_shortstops = self.client.put(
            self.url(), {"players": [self.spot(self.player_m, 1, "SS"), self.spot(self.player2_m, 2, "SS")]}
        )
        self.assertEqual(two_shortstops.status_code, status.HTTP_400_BAD_REQUEST)

        two_ehs = self.client.put(
            self.url(), {"players": [self.spot(self.player_m, 1, "EH"), self.spot(self.player2_m, 2, "EH")]}
        )
        self.assertEqual(two_ehs.status_code, status.HTTP_200_OK, two_ehs.data)

    def test_unknown_position_rejected(self):
        self.login(self.manager)
        response = self.client.put(self.url(), {"players": [self.spot(self.player_m, 1, "QB")]})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_add_another_teams_player(self):
        self.login(self.manager)
        response = self.client.put(
            self.url(), {"players": [self.spot(self.player_m, 1, "SS"), self.spot(self.ringer_m, 2, "CF")]}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(LineupSpot.objects.filter(membership=self.ringer_m).exists())

    def test_inactive_player_rejected(self):
        self.player2_m.is_active = False
        self.player2_m.save()
        self.login(self.manager)
        response = self.client.put(self.url(), {"players": [self.spot(self.player2_m, 1, "SS")]})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_save_keeps_existing_lineup(self):
        self.login(self.manager)
        self.client.put(self.url(), {"players": [self.spot(self.player_m, 1, "SS")]})
        self.client.put(self.url(), {"players": [self.spot(self.player_m, 1, "SS"), self.spot(self.ringer_m, 2, "CF")]})
        self.assertEqual(
            list(LineupSpot.objects.filter(lineup__game=self.game).values_list("membership_id", flat=True)),
            [self.player_m.id],
        )

    def test_player_cannot_change_batting_order(self):
        self.login(self.player_user)
        response = self.client.put(self.url(), {"players": [self.spot(self.player_m, 1, "SS")]})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_warns_when_player_said_no(self):
        GameAvailability.objects.create(game=self.game, membership=self.player2_m, status="no")
        self.login(self.manager)
        response = self.client.put(self.url(), {"players": [self.spot(self.player2_m, 1, "SS")]})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["warnings"]), 1)

    def test_cancelled_game_has_no_lineup_edits(self):
        self.game.status = Game.Status.CANCELLED
        self.game.save()
        self.login(self.manager)
        response = self.client.put(self.url(), {"players": [self.spot(self.player_m, 1, "SS")]})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
