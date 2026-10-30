from django.urls import path

from . import views

app_name = "stats"

urlpatterns = [
    path("dashboard/", views.dashboard, name="dashboard"),
    path("search/", views.search_view, name="search"),
    path("logs/", views.logs_view, name="logs"),
]
