from django.urls import path

from . import views

urlpatterns = [
    path("", views.question_list, name="question-list"),
    path("questions/new/", views.question_create, name="question-create"),
    path("questions/<uuid:pk>/", views.question_detail, name="question-detail"),
    path("questions/<uuid:pk>/edit/", views.question_edit, name="question-edit"),
    path("questions/<uuid:pk>/archive/", views.question_archive, name="question-archive"),
    path("knowledge-cards/", views.knowledge_card_list, name="knowledge-card-list"),
    path("knowledge-cards/new/", views.knowledge_card_create, name="knowledge-card-create"),
    path("knowledge-cards/<uuid:pk>/", views.knowledge_card_detail, name="knowledge-card-detail"),
    path("knowledge-cards/<uuid:pk>/edit/", views.knowledge_card_edit, name="knowledge-card-edit"),
]
