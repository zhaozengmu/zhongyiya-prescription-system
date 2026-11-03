from django.urls import path

from . import views

app_name = "clinic"

urlpatterns = [
    # 医师诊室
    path("workbench/", views.workbench, name="workbench"),
    path("registrations/", views.registration_list, name="registration_list"),
    path("registrations/new/", views.registration_create, name="registration_create"),
    path("registrations/<int:pk>/call/", views.registration_call, name="registration_call"),
    path("registrations/<int:pk>/cancel/", views.registration_cancel, name="registration_cancel"),
    path("registrations/<int:pk>/pay/", views.registration_pay, name="registration_pay"),
    # 电子病历
    path("records/new/<int:reg_id>/", views.record_create, name="record_create"),
    path("records/<int:pk>/", views.record_detail, name="record_detail"),
    path("records/<int:pk>/edit/", views.record_edit, name="record_edit"),
    # 患者自助
    path("my/visits/", views.my_visits, name="my_visits"),
    path("my/appointment/", views.appointment_create, name="appointment_create"),
    # 病案服务申请
    path("requests/", views.request_list, name="request_list"),
    path("requests/new/", views.request_create, name="request_create"),
    path("requests/<int:pk>/", views.request_detail, name="request_detail"),
    path("requests/<int:pk>/handle/", views.request_handle, name="request_handle"),
]
