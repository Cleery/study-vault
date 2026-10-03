from django.urls import path

from . import views

urlpatterns = [
    path("", views.question_list, name="question-list"),
    path("markdown-preview/", views.markdown_preview, name="markdown-preview"),
    path("questions/new/", views.question_create, name="question-create"),
    path("questions/<uuid:pk>/", views.question_detail, name="question-detail"),
    path("questions/<uuid:pk>/edit/", views.question_edit, name="question-edit"),
    path("questions/<uuid:pk>/archive/", views.question_archive, name="question-archive"),
    path("tags/", views.tag_manage, name="tag-manage"),
    path("tags/suggestions/", views.tag_suggestions, name="tag-suggestions"),
    path("tags/new/", views.tag_manage, name="tag-create"),
    path("tags/<uuid:pk>/edit/", views.tag_manage, name="tag-edit"),
    path("tags/<uuid:pk>/rename/", views.tag_rename, name="tag-rename"),
    path("tags/<uuid:pk>/merge/", views.tag_merge, name="tag-merge"),
    path("tags/<uuid:pk>/merge/confirm/", views.tag_merge_confirm, name="tag-merge-confirm"),
    path("tags/<uuid:pk>/archive/", views.tag_archive, name="tag-archive"),
    path("tags/<uuid:pk>/archive/confirm/", views.tag_archive_confirm, name="tag-archive-confirm"),
    path("knowledge-cards/", views.knowledge_card_list, name="knowledge-card-list"),
    path("knowledge-cards/new/", views.knowledge_card_create, name="knowledge-card-create"),
    path("knowledge-cards/<uuid:pk>/", views.knowledge_card_detail, name="knowledge-card-detail"),
    path("knowledge-cards/<uuid:pk>/edit/", views.knowledge_card_edit, name="knowledge-card-edit"),
    path("knowledge-cards/<uuid:pk>/delete/", views.knowledge_card_delete, name="knowledge-card-delete"),
    path("review/", views.review_list, name="review-list"),
    path("review/<uuid:pk>/", views.review_detail, name="review-detail"),
    path("stats/", views.stats, name="stats"),
]
