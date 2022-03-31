"""Admin panel for Completion API and Progress Stats"""
from csv import writer as csv_writer
from datetime import datetime

from completion.models import BlockCompletion
from django.contrib import admin
from django.http import HttpResponse

from openedx.features.pakx.lms.overrides.models import ContactUs, CourseProgressStats


def download_as_csv(model_admin, request, queryset):    # pylint: disable=unused-argument
    opts = model_admin.model._meta  # pylint: disable=protected-access
    content_disposition = 'attachment; filename={}.csv'.format(opts.verbose_name)
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = content_disposition
    writer = csv_writer(response)

    fields = [field for field in opts.get_fields() if not field.many_to_many and not field.one_to_many]
    # Write a first row with header information
    writer.writerow([field.verbose_name for field in fields])
    # Write data rows
    for obj in queryset:
        data_row = []
        for field in fields:
            value = getattr(obj, field.name)
            if isinstance(value, datetime):
                value = value.strftime('%d/%m/%Y')
            data_row.append(value)
        writer.writerow(data_row)
    return response


download_as_csv.short_description = 'Download as CSV'


class BlockCompletionAdmin(admin.ModelAdmin):
    """
    Admin interface for the BlockCompletion object.
    """
    list_display = ('user', 'context_key', 'block_type', 'block_key', 'completion', 'created', 'modified')
    search_fields = ('user__username', 'block_type')

    class Meta(object):
        """
        Meta class for BlockCompletion admin model
        """
        model = BlockCompletion


admin.site.register(BlockCompletion, BlockCompletionAdmin)


@admin.register(CourseProgressStats)
class CourseProgressStatsAdmin(admin.ModelAdmin):
    """
    Admin interface for CourseProgressStats object
    """

    list_display = ['user_email', 'course_title', 'email_reminder_status',
                    'progress', 'grade', 'completion_date']
    search_fields = ['email_reminder_status', 'progress']
    list_filter = ['email_reminder_status', 'progress', 'enrollment__user__profile__organization',
                   'enrollment__course__display_name']
    actions = [download_as_csv]

    @staticmethod
    def user_email(obj):
        """user name"""
        return obj.enrollment.user.email

    @staticmethod
    def course_title(obj):
        """ course name """
        return obj.enrollment.course.display_name


@admin.register(ContactUs)
class ContactUsAdmin(admin.ModelAdmin):
    """
    Admin interface for ContactUs records
    """

    list_display = ['full_name', 'email', 'phone', 'organization', 'created_by', 'created_at']
    search_fields = ['created_by', 'name', 'email']
    list_filter = ['created_at', 'organization']
