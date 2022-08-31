"""
Progress Email related signal handlers.
"""

from django.dispatch import receiver

from lms.djangoapps.verify_student.models import ManualVerification
from openedx.features.pakx.lms.overrides.tasks import (
    add_enrollment_record,
    remove_enrollment_record,
    verify_user_and_change_enrollment
)
from student.models import EnrollStatusChange
from student.signals import ENROLL_STATUS_CHANGE


def user_already_verified(user):
    """Check if the user is already verified."""
    return bool(ManualVerification.objects.filter(user=user))


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
