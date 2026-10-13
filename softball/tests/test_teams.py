from rest_framework import status

from softball.models import Team, TeamMembership

from .base import TeamFixture, User


class TeamTests(TeamFixture):
    def test_creating_a_team_makes_the_creator_its_manager(self):
        self.login(self.player_user)
        response = self.client.post("/api/teams/", {"name": "Designated Hitters"})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["my_role"], "manager")

        team = Team.objects.get(pk=response.data["id"])
        membership = team.memberships.get()
        self.assertEqual(membership.user, self.player_user)
        self.assertEqual(membership.role, TeamMembership.Role.MANAGER)

    def test_team_list_only_includes_my_teams(self):
        self.login(self.player_user)
        response = self.client.get("/api/teams/")
        self.assertEqual([t["id"] for t in response.data], [self.team.id])
        self.assertEqual(response.data[0]["my_role"], "player")

    def test_player_cannot_rename_or_delete_team(self):
        self.login(self.player_user)
        self.assertEqual(
            self.client.patch(f"/api/teams/{self.team.id}/", {"name": "Mine now"}).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(self.client.delete(f"/api/teams/{self.team.id}/").status_code, status.HTTP_403_FORBIDDEN)

    def test_manager_can_create_season(self):
        self.login(self.manager)
        response = self.client.post(
            f"/api/teams/{self.team.id}/seasons/", {"name": "Summer 2027", "start_date": "2027-05-01"}
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["team"], self.team.id)

    def test_season_validation(self):
        self.login(self.manager)
        url = f"/api/teams/{self.team.id}/seasons/"
        backwards = self.client.post(url, {"name": "Oops", "start_date": "2027-05-01", "end_date": "2027-04-01"})
        self.assertEqual(backwards.status_code, status.HTTP_400_BAD_REQUEST)
        dupe = self.client.post(url, {"name": "fall 2026", "start_date": "2026-09-01"})
        self.assertEqual(dupe.status_code, status.HTTP_400_BAD_REQUEST)

    def test_player_cannot_create_season(self):
        self.login(self.player_user)
        response = self.client.post(
            f"/api/teams/{self.team.id}/seasons/", {"name": "Summer 2027", "start_date": "2027-05-01"}
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class RosterTests(TeamFixture):
    def test_roster_lists_active_members(self):
        self.player2_m.is_active = False
        self.player2_m.save()
        self.login(self.player_user)

        response = self.client.get(f"/api/teams/{self.team.id}/roster/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual({m["display_name"] for m in response.data}, {"Mia", "Pat", "Ursula"})

        with_inactive = self.client.get(f"/api/teams/{self.team.id}/roster/?include_inactive=1")
        self.assertEqual(len(with_inactive.data), 4)

    def test_manager_adds_unregistered_player(self):
        self.login(self.manager)
        response = self.client.post(
            f"/api/teams/{self.team.id}/roster/",
            {"display_name": "Newbie", "season": self.season.id, "jersey_number": 42},
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertIsNone(response.data["account"])
        self.assertEqual(response.data["role"], "player")

    def test_manager_links_registered_account_and_name_defaults(self):
        newcomer = User.objects.create_user("newcomer", first_name="Nia", last_name="Cole")
        self.login(self.manager)
        response = self.client.post(
            f"/api/teams/{self.team.id}/roster/", {"username": "newcomer", "season": self.season.id}
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["display_name"], "Nia Cole")
        self.assertEqual(response.data["account"], "newcomer")
        self.assertTrue(TeamMembership.objects.filter(team=self.team, user=newcomer).exists())

    def test_duplicate_membership_for_same_season_is_rejected(self):
        self.login(self.manager)
        response = self.client.post(
            f"/api/teams/{self.team.id}/roster/", {"username": "player", "season": self.season.id}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_attach_membership_to_another_teams_season(self):
        self.login(self.manager)
        response = self.client.post(
            f"/api/teams/{self.team.id}/roster/", {"display_name": "Sneaky", "season": self.other_season.id}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("season", response.data)

    def test_player_cannot_add_roster_members_or_edit_memberships(self):
        self.login(self.player_user)
        add = self.client.post(f"/api/teams/{self.team.id}/roster/", {"display_name": "Friend"})
        self.assertEqual(add.status_code, status.HTTP_403_FORBIDDEN)
        promote = self.client.patch(f"/api/memberships/{self.player_m.id}/", {"role": "manager"})
        self.assertEqual(promote.status_code, status.HTTP_403_FORBIDDEN)

    def test_manager_updates_membership(self):
        self.login(self.manager)
        response = self.client.patch(
            f"/api/memberships/{self.player_m.id}/", {"jersey_number": 99, "is_substitute": True}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.player_m.refresh_from_db()
        self.assertEqual(self.player_m.jersey_number, 99)
        self.assertTrue(self.player_m.is_substitute)

    def test_last_manager_cannot_step_down(self):
        self.login(self.manager)
        demote = self.client.patch(f"/api/memberships/{self.manager_m.id}/", {"role": "player"})
        self.assertEqual(demote.status_code, status.HTTP_400_BAD_REQUEST)
        deactivate = self.client.patch(f"/api/memberships/{self.manager_m.id}/", {"is_active": False})
        self.assertEqual(deactivate.status_code, status.HTTP_400_BAD_REQUEST)

        # With a second manager in place it's fine.
        self.client.patch(f"/api/memberships/{self.player_m.id}/", {"role": "manager"})
        demote = self.client.patch(f"/api/memberships/{self.manager_m.id}/", {"role": "player"})
        self.assertEqual(demote.status_code, status.HTTP_200_OK, demote.data)

    def test_deactivated_member_loses_access(self):
        self.player_m.is_active = False
        self.player_m.save()
        self.login(self.player_user)
        self.assertEqual(self.client.get(f"/api/teams/{self.team.id}/").status_code, status.HTTP_404_NOT_FOUND)
