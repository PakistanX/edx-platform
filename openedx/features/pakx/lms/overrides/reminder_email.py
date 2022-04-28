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

log = getLogger(__name__)


def check_reminder_status(user_reminder_date, last_date_for_reminder, today):
    """Check reminder email needs to be sent or not to the learner."""

    send_reminder = True
    reminder_date = None

    if today > last_date_for_reminder:
        log.info('Last date for reminder have been passed\nToday:{} Last Date:{}'.format(today, last_date_for_reminder))
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


def send_reminder_email(course_key, enrollment):
    """Send reminder email to user."""

    user = enrollment.user
    log.info("Sending reminder email to user:{}".format(user))
    site = Site.objects.get_current()
    message_context = get_base_template_context(site, user)
    message_context.update({
        'course_name': enrollment.course.display_name,
        'course_url': "https://{domain}/courses/{course_id}/courseware".format(
            domain=site.domain,
            course_id=course_key
        ),
        'end_date': enrollment.course.end_date.date() if enrollment.course.end_date else None
    })

    msg = CourseReminder().personalize(
        recipient=Recipient(username=user.username, email_address=user.email),
        language=settings.LANGUAGE_CODE,
        user_context=message_context
    )

    with emulate_http_request(site=site, user=user):
        ace.send(msg)


def check_and_send_emails(progress_stats):
    """Check if a reminder email should be send according to settings."""

    course_key = text_type(progress_stats.enrollment.course_id)
    log.info('\n\nChecking reminder email for {}:{}'.format(progress_stats.enrollment.user.id, course_key))

    days_till_next_reminder = progress_stats.enrollment.course.custom_settings.days_till_next_reminder
    reminder_stop_date = progress_stats.enrollment.course.custom_settings.reminder_stop_date
    today = timezone.now().date()

    send_reminder, old_date = check_reminder_status(progress_stats.next_reminder_date, reminder_stop_date, today)
    date_changed = False

    if send_reminder:
        progress_stats.next_reminder_date = old_date + timedelta(days=days_till_next_reminder)
        date_changed = True
    if today == reminder_stop_date:
        progress_stats.next_reminder_date = None
        date_changed = True
    if date_changed:
        progress_stats.save()
    if send_reminder:
        send_reminder_email(course_key, progress_stats.enrollment)

    log.info('\n\n')
