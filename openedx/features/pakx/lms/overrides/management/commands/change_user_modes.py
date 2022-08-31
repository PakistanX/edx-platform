"""Management command to update a learner's enrollment mode."""

from logging import getLogger
from django.core.management.base import BaseCommand
from openedx.features.pakx.lms.overrides.tasks import change_enrolement_modes

log = getLogger(__name__)


class Command(BaseCommand):
    """
    A management command that checks learners enrolled in verified courses and changes their mode from audit to honor.

    Sometimes our training managers will be enrolling learners in verified courses through admin panel. These learners
    are by default enrolled with an audit mode that prevents them from accessing certificates. This command will change
    modes of such users to honor so that they can access their certificates.
    """

    help = "Change enrollment mode from audit to honor for verified courses"

    def handle(self, *args, **options):
        log.info("\n\nStaring command to change enrollment modes")
        change_enrolement_modes()
