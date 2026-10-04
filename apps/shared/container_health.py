"""A minimal, public database readiness probe with no deployment details."""
from django.db import DatabaseError, connection
from django.http import JsonResponse
from django_bolt import AllowAny, BoltAPI

from .bolt_pages import page_endpoint
from .request_logging import configure_api_logging

api = BoltAPI(trailing_slash="keep", django_middleware=True)
configure_api_logging(api)


@api.get("/healthz", name="container_health", guards=[AllowAny()])
@page_endpoint
def healthz(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            ready = cursor.fetchone() == (1,)
    except DatabaseError:
        ready = False
    response = JsonResponse({"status": "ready" if ready else "unavailable"}, status=200 if ready else 503)
    response["Cache-Control"] = "no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response
