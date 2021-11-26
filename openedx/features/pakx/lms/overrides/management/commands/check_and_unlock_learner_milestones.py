"""Management command to check and unlock subsections based on days"""

from logging import getLogger

from django.core.management.base import BaseCommand
from django.forms.models import model_to_dict
from django.utils import timezone
from milestones import api as milestones_api
from milestones.exceptions import InvalidMilestoneException
from opaque_keys.edx.keys import CourseKey
from six import text_type

from openedx.core.lib.gating.api import get_prerequisites
from openedx.features.pakx.lms.overrides.models import CourseProgressStats
from openedx.features.pakx.lms.overrides.utils import get_course_progress_and_unlock_date

log = getLogger(__name__)


class Command(BaseCommand):
    """A management command that checks if learners have completed their prereqs and unlocks subsections."""

    help = "Check and unlock subsections based on prereqs and unlock days"

    def handle(self, *args, **options):
        log.info("Starting command to check and unlock subsections based on days defined")

        progress_models = CourseProgressStats.objects.filter(progress__lt=100).select_related('enrollment')
        log.info("Fetching records, found {} active models".format(len(progress_models)))
        for item in progress_models:
            self._check_and_unlock_user_milestone(item.enrollment.user, text_type(item.enrollment.course_id))

    def _check_and_unlock_user_milestone(self, user, course_key):
        """Checks if pre req for locked subsection have been completed."""

        course_key = CourseKey.from_string(course_key)
        final_subsection, pre_req_for_final = self._get_subsections(get_prerequisites(course_key))

        if not final_subsection:
            log.info('No final milestone found for course:{}'.format(course_key))
            return

        try:
            if milestones_api.user_has_milestone(model_to_dict(user), pre_req_for_final):
                self._unlock_or_add_unlock_date(user, course_key, final_subsection)
        except InvalidMilestoneException:
            log.info('User:{} for course:{} have milestone:{}'.format(user.email, course_key, pre_req_for_final))

    def _get_subsections(self, milestones):
        """Get final subsection and its pre-req subsection"""

        final_subsection = self._get_final_subsection(milestones)

        if final_subsection:
            for milestone in milestones:
                if milestone['content_id'] == final_subsection['content_id']:
                    return final_subsection, milestone

        return final_subsection, None

    @staticmethod
    def _unlock_or_add_unlock_date(user, course_key, final_milestone):
        """Check and unlock subsection if unlock date has been specified and fulfilled."""

        date_to_unlock, course_progress = get_course_progress_and_unlock_date(user.id, course_key)

        if not date_to_unlock or not course_progress:
            return

        if not course_progress.unlock_subsection_on:
            course_progress.unlock_subsection_on = date_to_unlock
            course_progress.save(update_fields=['unlock_subsection_on'])
        elif timezone.now() >= course_progress.unlock_subsection_on:
            milestones_api.add_user_milestone(model_to_dict(user), final_milestone)
            log.info('Added Milestone for user:{} and course:{} where dates were:'.format(user.email, course_key))
            log.info('date_to_unlock: {}\tdate_now: {}'.format(date_to_unlock, timezone.now()))

    @staticmethod
    def _get_final_subsection(milestones):
        """Get final subsection of course that is a pre-req of itself."""

        for milestone in milestones:
            if milestone['content_id'] == milestone['namespace'].split('.gating')[0]:
                return milestone

        return None
