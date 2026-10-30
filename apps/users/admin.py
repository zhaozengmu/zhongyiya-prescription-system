"""Django Admin 注册，便于管理员维护基础数据。"""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Doctor, Permission, Pharmacist, Role, RolePermission, User, UserRole


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ("username", "get_full_name", "phone", "is_active", "is_staff")
    fieldsets = UserAdmin.fieldsets + (
        ("中医业务字段", {"fields": ("phone", "failed_attempts", "locked_until")}),
    )


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "description")


@admin.register(Permission)
class PermissionAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "level")
    list_filter = ("level",)


@admin.register(RolePermission)
class RolePermissionAdmin(admin.ModelAdmin):
    list_display = ("role", "permission")


@admin.register(UserRole)
class UserRoleAdmin(admin.ModelAdmin):
    list_display = ("user", "role")


@admin.register(Doctor)
class DoctorAdmin(admin.ModelAdmin):
    list_display = ("user", "license_no", "department", "title", "is_active")


@admin.register(Pharmacist)
class PharmacistAdmin(admin.ModelAdmin):
    list_display = ("user", "license_no", "is_active")
