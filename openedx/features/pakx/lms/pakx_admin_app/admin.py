"""
Django admin for the PakX Admin Panel app.
"""
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.forms import BaseInlineFormSet
from django.utils.html import format_html

from .course_link_api import report_url
from .course_links import link_url, slug_mappings
from .models import (
    CourseLink,
    CourseLinkClient,
    CourseLinkDailyStat,
    MAUReport,
    MAUReportExcludedUser,
    TrendingCourse
)


@admin.register(TrendingCourse)
class TrendingCourseAdmin(admin.ModelAdmin):
    """Curate the dashboard "Trending Courses" tab (global list)."""
    list_display = ('course', 'position', 'is_active', 'created_at')
    list_filter = ('is_active',)
    list_editable = ('position', 'is_active')
    search_fields = ('course__id', 'course__display_name')
    autocomplete_fields = ('course',)
    ordering = ('position', '-created_at')


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


def _hyperlink(url):
    """Clickable link (new tab) for an absolute URL, or a dash when there is none."""
    return format_html('<a href="{0}" target="_blank" rel="noopener">{0}</a>', url) if url else '-'


class NoDeleteAdmin(admin.ModelAdmin):
    """Deleting would cascade away a client's stats; links are switched off with is_active, like in the API."""

    def has_delete_permission(self, request, obj=None):
        return False


class SlugMappingsMixin(object):
    """The ``url`` column needs the slug maps; load them once per request instead of per row."""
    _slug_mappings = None

    def get_queryset(self, request):
        # shortcut: one admin instance serves all requests, so a racing request may overwrite this with an
        # equal copy of the same configuration; harmless, revisit if the maps ever depend on the request
        self._slug_mappings = slug_mappings()
        return super(SlugMappingsMixin, self).get_queryset(request)

    def url(self, obj):
        # plain text on purpose: a click from the admin would count as a visit
        return link_url(obj, self._slug_mappings) if obj.pk else '-'


class CourseLinkInlineFormSet(BaseInlineFormSet):
    """Reject rows that define the same link twice; the database check only sees saved rows."""

    def clean(self):
        super(CourseLinkInlineFormSet, self).clean()
        seen = set()
        for form in self.forms:
            data = getattr(form, 'cleaned_data', None)
            if not data or data.get('DELETE') or not data.get('value'):
                continue
            value = data['value'].strip().lower()
            if data.get('kind') == CourseLink.ALIAS:
                key = (CourseLink.ALIAS, value)
            else:
                key = (CourseLink.UTM, value, data.get('course')) + tuple(
                    (data.get(field) or '').strip().lower() for field in CourseLink.UTM_EXTRA_FIELDS
                )
            if key in seen:
                raise ValidationError('Two rows define the same link.')
            seen.add(key)


class CourseLinkInline(SlugMappingsMixin, admin.TabularInline):
    """Add links to a client here; existing links are read-only (change them in the API or switch them off)."""
    model = CourseLink
    formset = CourseLinkInlineFormSet
    extra = 0
    can_delete = False
    fields = ('kind', 'value', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content', 'course', 'is_active', 'url')
    readonly_fields = ('url',)
    autocomplete_fields = ('course',)

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(CourseLinkClient)
class CourseLinkClientAdmin(NoDeleteAdmin):
    """Clients that get tracked links; the report URL is what is shared with them."""
    list_display = ('name', 'organization', 'is_report_enabled', 'report_url')
    list_filter = ('is_report_enabled',)
    list_select_related = ('organization',)
    search_fields = ('name', 'organization__name', 'organization__short_name')
    readonly_fields = ('report_url', 'created_at')
    fields = ('name', 'organization', 'is_report_enabled', 'report_url', 'created_at')
    inlines = (CourseLinkInline,)

    def report_url(self, obj):  # pylint: disable=method-hidden
        return _hyperlink(report_url(obj)) if obj.pk else '-'


@admin.register(CourseLink)
class CourseLinkAdmin(SlugMappingsMixin, NoDeleteAdmin):
    list_display = ('client', 'label', 'course', 'is_active', 'url')
    list_filter = ('kind', 'is_active', 'client')
    list_select_related = ('client',)
    search_fields = (
        'value', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content', 'client__name', 'course__id',
        'course__display_name',
    )
    autocomplete_fields = ('course',)
    readonly_fields = ('url',)

    def get_readonly_fields(self, request, obj=None):
        """Once created only is_active changes, like the API: client, course and value are what clients were given."""
        if obj is None:
            return self.readonly_fields
        return ('client', 'course', 'kind', 'value') + CourseLink.UTM_EXTRA_FIELDS + self.readonly_fields


@admin.register(CourseLinkDailyStat)
class CourseLinkDailyStatAdmin(NoDeleteAdmin):
    list_display = ('link', 'date', 'hits', 'unique_visitors')
    list_filter = ('date', 'link__client')
    list_select_related = ('link__client',)
    readonly_fields = ('link', 'date', 'hits', 'unique_visitors')

    def has_add_permission(self, request):
        return False
