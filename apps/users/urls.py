from django.urls import path

from . import views

app_name = "users"

urlpatterns = [
    path("", views.landing, name="index"),
    path("welcome/", views.landing, name="landing"),
    path("login/", views.login_view, name="login"),
    path("captcha/", views.captcha_view, name="captcha"),
    path("register/", views.register_view, name="register"),
    path("pending/", views.pending_review, name="pending"),
    path("applications/", views.application_list, name="application_list"),
    path("applications/<int:pk>/review/", views.application_review, name="application_review"),
    path("logout/", views.logout_view, name="logout"),
    path("doctors/", views.doctor_list, name="doctor_list"),
    path("profile/", views.profile_view, name="profile"),
    path("home/", views.home_redirect, name="home"),
]
