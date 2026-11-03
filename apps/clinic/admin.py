from django.contrib import admin

from .models import MedicalRecord, MedicalRecordRequest, Registration


@admin.register(Registration)
class RegistrationAdmin(admin.ModelAdmin):
    list_display = ("reg_no", "patient", "doctor", "visit_date", "time_slot", "status", "queue_no")
    list_filter = ("status", "reg_type", "source", "visit_date")
    search_fields = ("reg_no", "patient__name", "doctor__user__first_name")


@admin.register(MedicalRecord)
class MedicalRecordAdmin(admin.ModelAdmin):
    list_display = ("record_no", "patient", "doctor", "tcm_diagnosis", "status", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("record_no", "patient__name", "tcm_diagnosis")


@admin.register(MedicalRecordRequest)
class MedicalRecordRequestAdmin(admin.ModelAdmin):
    list_display = ("request_no", "patient", "request_type", "status", "delivery", "created_at")
    list_filter = ("status", "request_type", "delivery")
    search_fields = ("request_no", "patient__name", "applicant_name")
