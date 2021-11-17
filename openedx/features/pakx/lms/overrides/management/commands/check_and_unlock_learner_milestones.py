"""Management command to check and unlock subsections based on days"""

from logging import getLogger

from django.core.management.base import BaseCommand

from openedx.features.pakx.lms.overrides.tasks import check_and_unlock_subsections

log = getLogger(__name__)


class Command(BaseCommand):
    """A management command that checks if learners have completed their prereqs and unlocks subsections."""

    help = "Check and unlock subsections based on prereqs and unlock days"

    def handle(self, *args, **options):
        log.info("Staring command to check and unlock subsections based on days defined")
        check_and_unlock_subsections()
