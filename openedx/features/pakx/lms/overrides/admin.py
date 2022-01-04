"""Admin panel for Completion API and Progress Stats"""

from completion.models import BlockCompletion
from django.contrib import admin

from openedx.features.pakx.lms.overrides.models import ContactUs, CourseProgressStats


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
