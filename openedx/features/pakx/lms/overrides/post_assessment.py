from datetime import timedelta
from logging import getLogger

from django.conf import settings
from django.contrib.sites.models import Site
from django.forms.models import model_to_dict
from django.utils import timezone
from edx_ace import ace
from edx_ace.recipient import Recipient
from milestones import api as milestones_api
from milestones.exceptions import InvalidMilestoneException
from opaque_keys.edx.keys import CourseKey

from openedx.core.djangoapps.ace_common.template_context import get_base_template_context
from openedx.core.lib.celery.task_utils import emulate_http_request
from openedx.core.lib.gating.api import find_gating_milestones
from openedx.features.pakx.cms.custom_settings.models import CourseOverviewContent
from openedx.features.pakx.lms.overrides.message_types import PostAssessment

from .models import CourseProgressStats

log = getLogger(__name__)


def check_and_unlock_user_milestone(user, course_key):
    """Checks if pre req for locked subsection have been completed."""

    course_key = CourseKey.from_string(course_key)
    final_subsection, pre_req_for_final = get_subsections(
        find_gating_milestones(course_key, relationship='requires')
    )

    if not final_subsection or not pre_req_for_final:
        log.info('No final milestone found for course:{}'.format(course_key))
        return

    user_milestones = milestones_api.get_user_milestones(model_to_dict(user), final_subsection['namespace'])

    try:
        if user_has_milestone(user_milestones, pre_req_for_final):
            unlock_or_add_unlock_date(user, course_key, user_milestones, final_subsection)
    except InvalidMilestoneException:
        log.info('User:{} for course:{} have milestone:{}'.format(user.email, course_key, pre_req_for_final))


def get_subsections(milestones):
    """Get final subsection and its pre-req subsection"""

    final_subsection = get_final_subsection(milestones)

    if final_subsection:
        for milestone in milestones:
            if milestone['content_id'] == final_subsection['content_id']:
                return final_subsection, milestone

    return final_subsection, None


def unlock_or_add_unlock_date(user, course_key, milestones, final_milestone):
    """Check and unlock subsection if unlock date has been specified and fulfilled."""

    course_progress = set_date_and_get_course_progress_stats(user.id, course_key)

    if not course_progress:
        return

    if (timezone.now() >= course_progress.unlock_subsection_on
            and not user_has_milestone(milestones, final_milestone)):

        milestones_api.add_user_milestone(model_to_dict(user), final_milestone)
        send_post_assessment_email(user, course_progress.enrollment.course, final_milestone['content_id'])

        log.info('Added Milestone for user:{} and course:{} where dates were:'.format(user.email, course_key))
        log.info('date_to_unlock: {}\tdate_now: {}'.format(course_progress.unlock_subsection_on, timezone.now()))


def set_date_and_get_course_progress_stats(user_id, course_key):
    """Set date to unlock if not already set and course progress stats."""

    try:
        course_overview_content = CourseOverviewContent.objects.get(course_id=course_key)
        date_to_unlock = timezone.now() + timedelta(days=course_overview_content.days_to_unlock)
        course_stats = CourseProgressStats.objects.filter(
            enrollment__user_id=user_id,
            enrollment__course_id=course_key
        ).first()

        if not course_stats.unlock_subsection_on:
            course_stats.unlock_subsection_on = date_to_unlock
            course_stats.save(update_fields=['unlock_subsection_on'])

        return course_stats

    except (CourseOverviewContent.DoesNotExist, CourseProgressStats.DoesNotExist):
        log.info(
            'Course Progress stats or Course Overview does not exist for user:{} and course:{}'.format(
                user_id,
                course_key
            )
        )
        return None


def user_has_milestone(milestones, milestone):
    """Check if user has required milestone."""

    for user_milestone in milestones:
        if user_milestone['namespace'] == milestone['namespace']:
            return True

    return False


def get_final_subsection(milestones):
    """Get final subsection of course that is a pre-req of itself."""

    for milestone in milestones:
        if milestone['content_id'] == milestone['namespace'].split('.gating')[0]:
            return milestone

    return None


def send_post_assessment_email(user, course, block_id):
    """Send email to user for subsection unlock."""

    log.info("Sending post assessment email to user:{}".format(user))
    site = Site.objects.get_current()
    message_context = get_base_template_context(site, user)
    message_context.update({
        'course_name': course.display_name,
        'course_url': "https://{domain}/courses/{course_id}/jump_to/{block_id}".format(
            domain=site.domain,
            course_id=course.id,
            block_id=block_id
        ),
        'image_url': "https://{}{}".format(site.domain, course.course_image_url)
    })

    msg = PostAssessment().personalize(
        recipient=Recipient(username=user.username, email_address=user.email),
        language=settings.LANGUAGE_CODE,
        user_context=message_context
    )

    with emulate_http_request(site=site, user=user):
        ace.send(msg)
