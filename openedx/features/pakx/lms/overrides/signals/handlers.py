"""
Progress Email related signal handlers.
"""

from django.dispatch import receiver
from social_django.models import UserSocialAuth

from openedx.features.pakx.lms.overrides.tasks import (
    add_enrollment_record,
    remove_enrollment_record,
    verify_user_and_change_enrollment,
    trigger_active_campaign_event
)
from student.models import EnrollStatusChange
from student.signals import ENROLL_STATUS_CHANGE


@receiver(ENROLL_STATUS_CHANGE)
def copy_active_course_enrollment(sender, event=None, user=None, **kwargs):  # pylint: disable=unused-argument
    """
    Awards enrollment badge to the given user on new enrollments.
    """
    course_key = str(kwargs.get('course_id', "Null"))
    if event == EnrollStatusChange.enroll:
        add_enrollment_record(user.id, course_key)
        verify_user_and_change_enrollment(user, course_key)
    elif event == EnrollStatusChange.unenroll:
        remove_enrollment_record(user.id, course_key)


@receiver(post_save, sender=UserSocialAuth)
def optimize_attachment_response_signal(sender, instance, created, **kwargs):
    """Signal to optimize the attachment response"""
    if created:
        user = instance.user
        trigger_active_campaign_event.delay('join_now', user.email, user_name=user.name)
