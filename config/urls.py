from django.http import HttpResponse
from django.urls import include, path


def health(request):
    return HttpResponse("ok")


urlpatterns = [
    path("", include("question_bank.urls")),
    path("health/", health, name="health"),
]
