"""Celery tasks to enroll users in courses and send registration email"""

from logging import getLogger

from celery import task
from django.conf import settings
from django.contrib.sites.models import Site
from django.db import DatabaseError, transaction
from edx_ace import Recipient, ace
from opaque_keys.edx.keys import CourseKey

from openedx.core.djangoapps.ace_common.template_context import get_base_template_context
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from student.models import CourseEnrollment

from .message_types import EnrolmentNotification
from .utils import get_accessible_users

log = getLogger(__name__)


def get_enrolment_email_message_context(user, courses):
    """
    return context for course enrolment notification email body
    """
    site = Site.objects.get_current()
    message_context = {
        'site_name': site.domain
    }
    message_context.update(get_base_template_context(site))
    message_context.update({
        'platform_name': configuration_helpers.get_value('PLATFORM_NAME', settings.PLATFORM_NAME),
        'username': user.username,
        'courses': courses,
    })
    return message_context


@task(name='send course enrolment email')
def send_course_enrolment_email(users, course_keys):
    """
    send a course enrolment notification via email
    """
    courses = ', '.join(course_keys)
    for user in users:
        message = EnrolmentNotification().personalize(
            recipient=Recipient(user.username, user.email),
            language='en',
            user_context=get_enrolment_email_message_context(user, courses),
        )
        ace.send(message)


@task(name='enroll_users')
def enroll_users(request_user, user_ids, course_keys):
    """
    Enroll users in courses
    :param request_user: (User) request user
    :param user_ids: (list<int>) user ids
    :param course_keys: (list<string>) course key
    """
    users_enrolled = []
    try:
        with transaction.atomic():
            for course_key in course_keys:
                for user in get_accessible_users(request_user).filter(id__in=user_ids):
                    CourseEnrollment.enroll(user,
                                            CourseKey.from_string(course_key))
                    users_enrolled.append(user)
        send_course_enrolment_email.delay(users_enrolled, course_keys)
        log.info("Enrolled user(s): {}".format(users_enrolled))
    except DatabaseError:
        log.info("Task terminated!")
