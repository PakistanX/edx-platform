"""
Progress Email related signal handlers.
"""

from django.dispatch import receiver

from course_modes.models import CourseMode
from lms.djangoapps.verify_student.models import ManualVerification
from openedx.features.pakx.lms.overrides.tasks import add_enrollment_record, remove_enrollment_record
from student.models import EnrollStatusChange
from student.signals import ENROLL_STATUS_CHANGE


def course_is_verified(course_key):
    """Check if course has a verified track."""
    return bool(CourseMode.objects.filter(course_id=course_key, mode_slug=CourseMode.VERIFIED))


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
        if course_is_verified(course_key) and user_already_verified(user) is False:
            ManualVerification.objects.create(
                status=u'approved',
                user=user,
                name='{}:{}'.format(user.id, course_key),
                reason='Verified after enrolling in {}'.format(course_key)
            )
    elif event == EnrollStatusChange.unenroll:
        remove_enrollment_record(user.id, course_key)
