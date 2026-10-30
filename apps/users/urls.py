from django.urls import path

from . import views

app_name = "users"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("register/", views.register_view, name="register"),
    path("logout/", views.logout_view, name="logout"),
    path("doctors/", views.doctor_list, name="doctor_list"),
    path("profile/", views.profile_view, name="profile"),
    path("home/", views.home_redirect, name="home"),
]
