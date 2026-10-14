from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework import status

from softball.models import Team

from .base import TeamFixture


def image_file(name="logo.png", size=(600, 300), fmt="PNG", color=(200, 30, 30, 255)):
    buffer = BytesIO()
    mode = "RGBA" if fmt == "PNG" else "RGB"
    Image.new(mode, size, color[: len(mode)]).save(buffer, format=fmt)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=f"image/{fmt.lower()}")


class TeamLogoTests(TeamFixture):
    def url(self, team=None):
        return f"/api/teams/{(team or self.team).id}/logo/"

    def upload(self, file, team=None):
        return self.client.put(self.url(team), {"logo": file}, format="multipart")

    def test_manager_uploads_logo_and_it_is_shrunk_to_png(self):
        self.login(self.manager)
        response = self.upload(image_file(size=(1200, 600), fmt="JPEG", name="logo.jpg"))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertIsNotNone(response.data["logo_version"])

        self.team.refresh_from_db()
        stored = Image.open(BytesIO(bytes(self.team.logo)))
        self.assertEqual(stored.format, "PNG")
        self.assertEqual(stored.size, (256, 128))
        self.assertEqual(self.team.logo_content_type, "image/png")

    def test_members_can_fetch_the_logo(self):
        self.login(self.manager)
        version = self.upload(image_file()).data["logo_version"]

        self.login(self.player_user)
        team = self.client.get(f"/api/teams/{self.team.id}/")
        self.assertEqual(team.data["logo_version"], version)

        response = self.client.get(self.url(), HTTP_ACCEPT="*/*")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertEqual(Image.open(BytesIO(response.content)).format, "PNG")

        cached = self.client.get(self.url(), HTTP_ACCEPT="*/*", HTTP_IF_NONE_MATCH=response["ETag"])
        self.assertEqual(cached.status_code, status.HTTP_304_NOT_MODIFIED)

    def test_no_logo_is_404_and_version_is_null(self):
        self.login(self.player_user)
        self.assertEqual(self.client.get(self.url()).status_code, status.HTTP_404_NOT_FOUND)
        self.assertIsNone(self.client.get(f"/api/teams/{self.team.id}/").data["logo_version"])

    def test_player_cannot_upload_or_remove(self):
        self.login(self.player_user)
        self.assertEqual(self.upload(image_file()).status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.client.delete(self.url()).status_code, status.HTTP_403_FORBIDDEN)

    def test_other_team_is_hidden(self):
        self.login(self.manager)
        self.assertEqual(self.upload(image_file(), team=self.other_team).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.client.get(self.url(self.other_team)).status_code, status.HTTP_404_NOT_FOUND)

    def test_rejects_files_that_are_not_images(self):
        self.login(self.manager)
        fake = SimpleUploadedFile("logo.png", b"definitely not a png", content_type="image/png")
        response = self.upload(fake)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("logo", response.data)
        self.assertIsNone(Team.objects.get(pk=self.team.pk).logo)

    def test_rejects_huge_files(self):
        self.login(self.manager)
        big = SimpleUploadedFile("logo.png", b"0" * (5 * 1024 * 1024 + 1), content_type="image/png")
        self.assertEqual(self.upload(big).status_code, status.HTTP_400_BAD_REQUEST)

    def test_manager_removes_logo(self):
        self.login(self.manager)
        self.upload(image_file())
        self.assertEqual(self.client.delete(self.url()).status_code, status.HTTP_204_NO_CONTENT)
        self.team.refresh_from_db()
        self.assertIsNone(self.team.logo)
        self.assertIsNone(self.team.logo_version)

    def test_team_list_still_works_with_logos(self):
        self.login(self.manager)
        self.upload(image_file())
        response = self.client.get("/api/teams/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(response.data[0]["logo_version"])
