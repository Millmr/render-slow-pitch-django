from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APITestCase

User = get_user_model()


class AuthTests(APITestCase):
    def setUp(self):
        cache.clear()  # reset login/register throttling between tests

    def test_register_returns_token_that_authenticates(self):
        response = self.client.post(
            "/api/auth/register/", {"username": "casey", "password": "slow-pitch-2026", "first_name": "Casey"}
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        token = response.data["token"]

        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
        me = self.client.get("/api/auth/me/")
        self.assertEqual(me.status_code, status.HTTP_200_OK)
        self.assertEqual(me.data["username"], "casey")

    def test_register_rejects_weak_password_and_duplicate_username(self):
        weak = self.client.post("/api/auth/register/", {"username": "casey", "password": "123"})
        self.assertEqual(weak.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("password", weak.data)

        User.objects.create_user("casey", password="slow-pitch-2026")
        dupe = self.client.post("/api/auth/register/", {"username": "CASEY", "password": "slow-pitch-2026"})
        self.assertEqual(dupe.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("username", dupe.data)

    def test_login_and_logout(self):
        User.objects.create_user("casey", password="slow-pitch-2026")
        bad = self.client.post("/api/auth/login/", {"username": "casey", "password": "wrong"})
        self.assertEqual(bad.status_code, status.HTTP_400_BAD_REQUEST)

        good = self.client.post("/api/auth/login/", {"username": "casey", "password": "slow-pitch-2026"})
        self.assertEqual(good.status_code, status.HTTP_200_OK)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {good.data['token']}")

        self.assertEqual(self.client.post("/api/auth/logout/").status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(self.client.get("/api/auth/me/").status_code, status.HTTP_401_UNAUTHORIZED)

    def test_me_requires_authentication(self):
        self.assertEqual(self.client.get("/api/auth/me/").status_code, status.HTTP_401_UNAUTHORIZED)
