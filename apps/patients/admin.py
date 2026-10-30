from django.contrib import admin

from .models import Patient


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ("patient_no", "name", "gender", "age", "pregnancy_status", "created_at")
    search_fields = ("patient_no", "name")
    list_filter = ("gender", "pregnancy_status", "is_deleted")
