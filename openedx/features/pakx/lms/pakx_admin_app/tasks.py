"""Celery tasks to enroll users in courses and send registration email"""

from logging import getLogger

from celery import task
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.core.mail.message import EmailMultiAlternatives
from django.db import DatabaseError, transaction
from django.urls import reverse
from edx_ace import Recipient, ace
from opaque_keys.edx.keys import CourseKey

from openedx.core.djangoapps.ace_common.template_context import get_base_template_context
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.lib.celery.task_utils import emulate_http_request
from student.models import CourseEnrollment

from .message_types import EnrolmentNotification
from .utils import create_user, get_org_users_qs

log = getLogger(__name__)


@task(name='send course enrolment email')
def send_course_enrolment_email(request_user_id, email_context_map):
    """
    send a course enrolment notification via email
    :param request_user_id: (int) request user id
    :param email_context_map: (list<dict>) contains dict with email context data
    """
    site = Site.objects.get_current()
    request_user = User.objects.filter(id=request_user_id).first()
    for context in email_context_map:
        with emulate_http_request(site, request_user):
            context.update({
                'site_name': site.domain,
                'dashboard_url': 'https://' + site.domain + '/dashboard',
                'platform_name': configuration_helpers.get_value('PLATFORM_NAME', settings.PLATFORM_NAME),
            })

            message = EnrolmentNotification().personalize(
                recipient=Recipient(context['username'], context['email']),
                language='en',
                user_context=context,
            )
            ace.send(message)


def send_bulk_registration_stats_email(email_msg, recipient):
    email_msg = EmailMultiAlternatives(
        to=[recipient], body=email_msg, subject='User Bulk Registration Stats',
    )
    try:
        email_msg.send()
    except Exception:  # pylint: disable=broad-except
        log.exception('Failed to send registration stats email to {}'.format(recipient))


@task(name='bulk_user_registration')
def bulk_user_registration(users_data, recipient, send_creation_email=True):
    def get_formatted_error_msg(errors, index):
        err_map = errors['response_errors']
        req_data = errors['req_data']

        profile_err_map = err_map.pop('profile', {})
        profile_req_data = req_data.pop('profile', {})

        lang_err = profile_err_map.pop('language_code', {})
        if lang_err:
            err_map.update({'language_code': [' |'.join(r) for r in lang_err.values()]})

        err_map.update(profile_err_map)
        req_data.update(profile_req_data)

        lang_err_msg = err_map.pop('language_code', None)
        formatted_errors = ['{}:({}) | {}'.format(f, req_data[f], '|'.join(err)) for f, err in err_map.items()]
        formatted_errors += ['language: {}'.format('|'.join(lang_err_msg))] if lang_err_msg else []

        user_key = req_data['email'] or req_data['username'] or profile_req_data.get('name') or index
        return {'msg': '\n'.join(formatted_errors), 'user_key': user_key}

    error_map = {}
    created_emails = []
    site = Site.objects.get_current()
    req_user = User.objects.get(email=recipient)

    for idx, user in enumerate(users_data, start=1):
        with emulate_http_request(site, req_user):
            is_created, user_data, user_password = create_user(user, next_url=reverse('account_settings'), send_creation_email=send_creation_email)
        if is_created:
            created_emails.append(user_data.email)
        else:
            error_map[idx] = {'response_errors': user_data, 'req_data': user}
        if user_password and is_created:        # used with sync call with delay
            user['user_password'] = user_password

    err_msg_t = "User's email/username/name or index in file: {user_key}\n{msg}"
    errors_msg = [err_msg_t.format(**get_formatted_error_msg(err, index)) for index, err in error_map.items()]

    success_msg_t = 'The following users have been created:\n{}'
    success_msg = success_msg_t.format('\n'.join(['email: {}'.format(email) for email in created_emails] or ['N/A']))

    email_msg = '{}\n\nThe following errors occurred:\n{}'.format(success_msg, '\n\n'.join(errors_msg or ['N/A']))
    send_bulk_registration_stats_email(email_msg, recipient)


@task(name='enroll_users')
def enroll_users(request_user_id, user_ids, course_keys_string):
    """
    Enroll users in courses
    :param request_user_id: (int) request user id
    :param user_ids: (list<int>) user ids
    :param course_keys_string: (list<string>) course key
    """
    request_user = User.objects.filter(id=request_user_id).first()
    if request_user:
        enrolled_email_data = []
        site = Site.objects.get_current()
        users_to_enroll = get_org_users_qs(request_user).filter(id__in=user_ids)
        try:
            with transaction.atomic():
                for course_key_string in course_keys_string:
                    course_key = CourseKey.from_string(course_key_string)
                    course_overview = CourseOverview.objects.get(id=CourseKey.from_string(course_key_string))
                    email_context = {
                        'course': course_overview.display_name,
                        'image_url': 'https://' if not settings.DEBUG else 'http://' + site.domain + course_overview.course_image_url,
                        'url': "https://{}/courses/{}/overview".format(site.domain, course_key_string),
                    }
                    for user in users_to_enroll:
                        try:
                            CourseEnrollment.enroll(user, course_key, check_access=True)
                            context = get_base_template_context(site, user=user)
                            context.update(email_context)
                            enrolled_email_data.append(context)
                        except Exception:  # pylint: disable=broad-except
                            pass
            send_course_enrolment_email.delay(request_user_id, enrolled_email_data)
        except DatabaseError:
            log.info("Task terminated!")
    else:
        log.info("Invalid request user id - Task terminated!")
