"""
Django admin for the PakX Admin Panel app.
"""
from django.contrib import admin

from .models import MAUReport, MAUReportExcludedUser


@admin.register(MAUReport)
class MAUReportAdmin(admin.ModelAdmin):
    list_display = (
        'month', 'organization_short_name', 'is_overall',
        'active_user_count', 'excluded_staff_count', 'created_at',
    )
    list_filter = ('is_overall', 'organization_short_name', 'month')
    search_fields = ('organization_short_name', 'organization_name')
    readonly_fields = (
        'organization_short_name', 'organization_name', 'is_overall', 'month',
        'active_user_count', 'excluded_staff_count', 'file_path', 'created_at',
    )


@admin.register(MAUReportExcludedUser)
class MAUReportExcludedUserAdmin(admin.ModelAdmin):
    list_display = ('username', 'organization_short_name')
    list_filter = ('organization_short_name',)
    search_fields = ('username', 'organization_short_name')
