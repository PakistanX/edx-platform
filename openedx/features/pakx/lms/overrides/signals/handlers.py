"""
Progress Email related signal handlers.
"""

from django.dispatch import receiver

from course_modes.models import CourseMode
from lms.djangoapps.verify_student.models import ManualVerification
from openedx.features.pakx.lms.overrides.tasks import (
    add_enrollment_record, remove_enrollment_record, change_enrollment_mode, manually_verify_user
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
        modes = CourseMode.objects.filter(course_id=course_key)
        if bool(modes.filter(mode_slug=CourseMode.VERIFIED)) and user_already_verified(user) is False:
            manually_verify_user(user, course_key)
            if len(modes) == 1:
                change_enrollment_mode(user.id, course_key)
    elif event == EnrollStatusChange.unenroll:
        remove_enrollment_record(user.id, course_key)
