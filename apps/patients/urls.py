from django.urls import path

from . import views

app_name = "patients"

urlpatterns = [
    path("", views.patient_list, name="patient_list"),
    path("<int:pk>/", views.patient_detail, name="patient_detail"),
    path("<int:pk>/edit/", views.patient_edit, name="patient_edit"),
    path("<int:pk>/delete/", views.patient_delete, name="patient_delete"),
]
