from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("teams", views.TeamViewSet, basename="team")
router.register("seasons", views.SeasonViewSet, basename="season")
router.register("memberships", views.MembershipViewSet, basename="membership")
router.register("games", views.GameViewSet, basename="game")

urlpatterns = router.urls
