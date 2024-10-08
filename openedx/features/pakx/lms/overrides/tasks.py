"""Celery tasks for to update user progress and send reminder emails"""

from logging import getLogger

from celery import task
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from edx_ace import ace
from edx_ace.recipient import Recipient
from six import text_type

from course_modes.models import CourseMode
from grades.api import CourseGradeFactory
from lms.djangoapps.verify_student.models import ManualVerification
from openedx.core.djangoapps.ace_common.template_context import get_base_template_context
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.lang_pref import LANGUAGE_KEY
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangoapps.user_api.preferences.api import get_user_preference
from openedx.core.lib.celery.task_utils import emulate_http_request
from openedx.features.pakx.lms.overrides.constants import SKIP_UPDATE_COURSE_PROGRESS_COURSE_IDS 
from openedx.features.pakx.lms.overrides.message_types import ContactUs, CourseProgress
from openedx.features.pakx.lms.overrides.models import CourseProgressStats
from openedx.features.pakx.lms.overrides.post_assessment import check_and_unlock_user_milestone
from openedx.features.pakx.lms.overrides.reminder_email import check_and_send_emails
from openedx.features.pakx.lms.overrides.utils import (
    create_dummy_request,
    get_course_progress_percentage,
    get_date_diff_in_days
)
from student.models import CourseEnrollment

log = getLogger(__name__)


def _get_email_message_context(data, user):
    """
    get basic context for email

    :returns: (dict) a dict object
    """

    site = Site.objects.get_current()
    message_context = get_base_template_context(site, user=user)
    message_context.update({
        'platform_name': configuration_helpers.get_value('PLATFORM_NAME', settings.PLATFORM_NAME),
        'course_name': data.get('course_name'),
        'completed': data.get('completed'),
        'status_message': data.get('status_message')
    })

    return message_context


@task(name='send_reminder_email')
def send_reminder_email(data, course_key):
    """
    A task to send reminder emails
    :param data: (dict) dict containing data for the email
    :param course_key: (CourseKey)
    """

    log.info("Sending course email to :{}, for:{}, progress:{}, complete:{}".format(
        data.get("email"), data.get("course_name"), data.get("course_progress"), data.get("completed")))
    site = Site.objects.get_current()
    course_url = '{url}{path}'.format(
        url=site.domain.strip(),
        path=reverse('course_root', kwargs={'course_id': course_key})
    )
    data['course_url'] = course_url
    user = User.objects.get(username=data.get('username'), email=data.get('email'))
    message_context = _get_email_message_context(data, user)
    log.info("**** user:{} message_context:{}".format(user, message_context))
    with emulate_http_request(site, user):
        msg = CourseProgress().personalize(
            recipient=Recipient(data.get('username'), data.get('email')),
            language=data.get('language'),
            user_context=message_context
        )
        ace.send(msg)


@task(name='send_contact_us_email')
def send_contact_us_email(data):
    """
    A task to send contact us data to support emails
    :param data: (dict) dict containing data for the email
    """

    log.info("Sending contact us email to support email ID")
    site = Site.objects.get_current()
    message_context = get_base_template_context(site)
    message_context.update(data)

    recipient = configuration_helpers.get_value('CONTACT_EMAIL', settings.CONTACT_EMAIL)
    msg = ContactUs().personalize(
        recipient=Recipient(username='', email_address=recipient),
        language=settings.LANGUAGE_CODE,
        user_context=message_context
    )

    ace.send(msg)


@task(name='add_enrollment_record')
def add_enrollment_record(user_id, course_id):
    """
    Add current enrollment record
    :param user_id: (str) user id
    :param course_id: (CourseKeyField) course key
    """

    enrollment = CourseEnrollment.objects.filter(user_id=user_id, course_id=course_id).first()
    if enrollment:
        _, created = CourseProgressStats.objects.get_or_create(enrollment=enrollment)
        if not created:
            log.info("Enrollment record for {} & User:{} already enrolled".format(course_id, user_id))


@task(name='remove_enrollment_record')
def remove_enrollment_record(user_id, course_id):
    """
    Remove current enrollment record
    :param user_id: (str) user name
    :param course_id: (CourseKeyField) course key
    """
    enrollment = CourseEnrollment.objects.filter(user_id=user_id, course_id=course_id).first()
    if enrollment:
        CourseProgressStats.objects.filter(enrollment=enrollment).delete()


# TODO: Remove this method when deployed on prod & enrollments are copied to stats
@task(name='copy_active_course_enrollments')
def copy_active_course_enrollments():
    """
    A task that copies active enrollments to CourseProgressStats model
    """

    active_enrollments = CourseEnrollment.objects.filter(is_active=True)
    log.info("Found {} active enrollment records, ".format(len(active_enrollments)))
    for enrollment in active_enrollments:
        CourseProgressStats.objects.get_or_create(enrollment=enrollment)


@task(name='update_course_progress_stats')
def update_course_progress_stats():
    """
    A task that checks all the current progress models & sends course completion or reminder emails
    """

    email_status_to_filter = [CourseProgressStats.NO_EMAIL_SENT, CourseProgressStats.REMINDER_SENT]
    progress_models = CourseProgressStats.objects.filter(
        Q(email_reminder_status__in=email_status_to_filter) | Q(progress__lt=100)
    ).select_related('enrollment', 'enrollment__user')
    log.info("Fetching records, found {} active models".format(len(progress_models)))

    for item in progress_models:
        user = item.enrollment.user
        course_id = item.enrollment.course_id
        # if course_id in SKIP_UPDATE_COURSE_PROGRESS_COURSE_IDS:
        #     continue
        course_progress = float(
            get_course_progress_percentage(create_dummy_request(Site.objects.get_current(), user),
                                           text_type(course_id)))
        course_overview = CourseOverview.get_from_id(course_id)
        grades = CourseGradeFactory().read(user=user, course_key=course_id)
        completed = course_progress >= 100
        fields_list = ['progress', 'grade']
        if course_progress >= 100:
            item.completion_date = timezone.now()
            fields_list.append('completion_date')
        item.progress = course_progress
        item.grade = grades.letter_grade

        data = {'course_name': course_overview.display_name, 'username': user.username,
                'email': user.email,
                'language': get_user_preference(user, LANGUAGE_KEY),
                'completed': completed,
                'status_message': "Completed" if completed else "Pending",
                'course_progress': course_progress}

        if data["completed"] and item.email_reminder_status != CourseProgressStats.COURSE_COMPLETED:
            # TODO: Un-comment send_reminder_email call when Email templates are finalized
            # send_reminder_email.delay(data, text_type(course_id))
            item.email_reminder_status = CourseProgressStats.COURSE_COMPLETED
            fields_list.append('email_reminder_status')
        elif course_overview.end_date:
            remaining_days = get_date_diff_in_days(course_overview.end_date)
            if remaining_days <= settings.COURSE_PROGRESS_REMINDER_EMAIL_DAYS and item.email_reminder_status == 0:
                # send_reminder_email.delay(data, text_type(course_id))
                item.email_reminder_status = CourseProgressStats.REMINDER_SENT
                fields_list.append('email_reminder_status')
        item.save(update_fields=fields_list)


@task(name='unlock_subsections')
def unlock_subsections():
    """Start checking and unlocking milestones"""

    progress_models = CourseProgressStats.objects.filter(progress__lt=100).select_related('enrollment')
    log.info("Fetching records, found {} active models".format(len(progress_models)))
    for item in progress_models:
        check_and_unlock_user_milestone(item.enrollment.user, text_type(item.enrollment.course_id))


@task(name='send_reminder_emails')
def send_reminder_emails():
    """Start checking and sending reminder emails"""

    progress_models = CourseProgressStats.objects.filter(
        progress__lt=100,
        enrollment__course__custom_settings__days_till_next_reminder__gt=0,
        enrollment__course__custom_settings__reminder_stop_date__isnull=False
    ).select_related('enrollment')
    log.info("Fetching records, found {} active models\n\n".format(len(progress_models)))
    for item in progress_models:
        check_and_send_emails(item)


@task(name='verify_user_and_change_enrollment')
def verify_user_and_change_enrollment(user, course_key):
    """Manually verify user for verified certificate and change enrollment mode if needed.."""
    modes = CourseMode.objects.filter(course_id=course_key)
    if bool(modes.filter(mode_slug=CourseMode.VERIFIED)):
        if not bool(ManualVerification.objects.filter(user=user)):
            ManualVerification.objects.create(
                status=u'approved',
                user=user,
                name='{}:{}'.format(user.id, course_key),
                reason='Verified after enrolling in {}'.format(course_key)
            )
        if len(modes) == 1:
            log.info('Starting mode change for user: {} and course {}'.format(user.id, course_key))
            CourseEnrollment.objects.filter(user_id=user.id, course=course_key, mode='audit').update(mode='verified')
