from django.urls import path

from . import views

urlpatterns = [
    path("patients", views.api_patient_list, name="api_patient_list"),
    path("patients/create", views.api_patient_create, name="api_patient_create"),
]
