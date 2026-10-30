from django.urls import path

from . import views

urlpatterns = [
    path("stats/overview", views.api_stats_overview, name="api_stats_overview"),
    path("search/prescriptions", views.api_search, name="api_search"),
    path("logs", views.api_logs, name="api_logs"),
    path("export/prescriptions", views.api_export, name="api_export"),
    path("export/herbs", views.api_export_herbs, name="api_export_herbs"),
    path("export/logs", views.api_export_logs, name="api_export_logs"),
    path("export/reviews", views.api_export_reviews, name="api_export_reviews"),
]
