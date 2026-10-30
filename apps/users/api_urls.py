from django.urls import path

from . import views

urlpatterns = [
    path("auth/login", views.api_login, name="api_login"),
    path("auth/logout", views.api_logout, name="api_logout"),
    path("doctors", views.api_doctors, name="api_doctors"),
]
