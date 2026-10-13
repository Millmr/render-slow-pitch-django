from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path


def health(request):
    """Unauthenticated liveness check for Docker/Render."""
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health/", health, name="health"),
    path("api/auth/", include("accounts.urls")),
    path("api/", include("softball.urls")),
]
