from datetime import timedelta
from logging import getLogger

from django.conf import settings
from django.contrib.sites.models import Site
from django.utils import timezone
from edx_ace import ace
from edx_ace.recipient import Recipient
from six import text_type

from openedx.core.djangoapps.ace_common.template_context import get_base_template_context
from openedx.core.lib.celery.task_utils import emulate_http_request
from openedx.features.pakx.lms.pakx_admin_app.message_types import CourseReminder
from student.helpers import get_resume_urls_for_enrollments

log = getLogger(__name__)


def check_and_send_emails(progress_stats):
    """Check if a reminder email should be send according to settings."""

    course_key = text_type(progress_stats.enrollment.course_id)
    log.info('\n\nChecking reminder email for {}:{}'.format(progress_stats.enrollment.user.id, course_key))

    days_to_wait_before_reminder = progress_stats.enrollment.course.custom_settings.days_to_wait_before_reminder
    last_date_of_reminder = progress_stats.enrollment.course.custom_settings.last_date_of_reminder

    send_reminder, updated_date = check_reminder_status(progress_stats.next_reminder_date, last_date_of_reminder)

    if send_reminder:
        progress_stats.next_reminder_date = updated_date + timedelta(days=days_to_wait_before_reminder)
        send_reminder_email(progress_stats.enrollment, last_date_of_reminder)
        progress_stats.save()

    log.info('\n\n')


def check_reminder_status(user_reminder_date, last_date_for_reminder):
    """Check if a learner's reminder email needs to be send or not."""

    send_reminder = True
    reminder_date = None
    today = timezone.now().date()
    if today > last_date_for_reminder:
        log.info('Last date for reminder have been passed')
        log.info('Today:{} Last Date:{}'.format(today, last_date_for_reminder))
        send_reminder = False
    elif user_reminder_date:
        if user_reminder_date == today:
            reminder_date = user_reminder_date
            log.info('Reminder date is today. Date:{}'.format(user_reminder_date))
        else:
            send_reminder = False
            log.info('Reminder date is not today. Date:{}'.format(user_reminder_date))
    else:
        log.info("No date found for user. Setting today's date:{}".format(today))
        reminder_date = today

    return send_reminder, reminder_date


def send_reminder_email(enrollment, end_date):
    """Send reminder email to user."""

    user = enrollment.user
    log.info("Sending reminder email to user:{}".format(user))
    site = Site.objects.get_current()
    message_context = get_base_template_context(site, user)
    message_context.update({
        'course_name': enrollment.course.display_name,
        'course_url': "https://{domain}{url}".format(
            domain=site.domain,
            url=get_resume_urls_for_enrollments(user, [enrollment])[enrollment.course_id]
        ),
        'end_date': end_date
    })

    msg = CourseReminder().personalize(
        recipient=Recipient(username=user.username, email_address=user.email),
        language=settings.LANGUAGE_CODE,
        user_context=message_context
    )

    with emulate_http_request(site=site, user=user):
        ace.send(msg)
