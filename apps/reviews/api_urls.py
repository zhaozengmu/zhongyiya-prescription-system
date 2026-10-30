from django.urls import path

from . import views

urlpatterns = [
    path("reviews/tasks", views.api_review_tasks, name="api_review_tasks"),
    path("reviews/<int:pk>", views.api_review_detail, name="api_review_detail"),
    path("reviews/<int:pk>/claim", views.api_review_claim, name="api_review_claim"),
    path("reviews/<int:pk>/approve", views.api_review_approve, name="api_review_approve"),
    path("reviews/<int:pk>/reject", views.api_review_reject, name="api_review_reject"),
    path("reviews/<int:pk>/history", views.api_review_history, name="api_review_history"),
    path("reviews/<int:pk>/dispense", views.api_dispense, name="api_dispense"),
    path("notifications", views.api_notifications, name="api_notifications"),
    path("notifications/<int:pk>/read", views.api_notification_read, name="api_notification_read"),
]
