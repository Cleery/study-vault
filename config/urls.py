from django.http import HttpResponse
from django.urls import path


def health(request):
    return HttpResponse("ok")


urlpatterns = [
    path("health/", health, name="health"),
]
