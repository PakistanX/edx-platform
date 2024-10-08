"""
Progress Email related signal handlers.
"""

from django.dispatch import receiver

from openedx.features.pakx.lms.overrides.tasks import (
    add_enrollment_record,
    remove_enrollment_record,
    verify_user_and_change_enrollment
)
from student.models import EnrollStatusChange
from student.signals import ENROLL_STATUS_CHANGE


@receiver(ENROLL_STATUS_CHANGE)
def copy_active_course_enrollment(sender, event=None, user=None, **kwargs):  # pylint: disable=unused-argument
    """
    Awards enrollment badge to the given user on new enrollments.
    """
    course_key = str(kwargs.get('course_id', "Null"))
    if event in [EnrollStatusChange.enroll, EnrollStatusChange.paid_complete, EnrollStatusChange.upgrade_complete]:
        add_enrollment_record(user.id, course_key)
        verify_user_and_change_enrollment(user, course_key)
    elif event == EnrollStatusChange.unenroll:
        remove_enrollment_record(user.id, course_key)
