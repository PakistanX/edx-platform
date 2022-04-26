"""Management command to update course progress stats"""

from logging import getLogger

from django.core.management.base import BaseCommand

from openedx.features.pakx.lms.overrides.tasks import send_reminder_emails

log = getLogger(__name__)


class Command(BaseCommand):
    """
    A management command that checks enrollment in active courses and sends email for course completion and
    reminder if course is about to end and no completed.
    """

    help = "Send reminder email to users according to settings authored in custom settings tab"

    def handle(self, *args, **options):
        log.info("\n\nStaring command to send reminder emails")
        send_reminder_emails()
