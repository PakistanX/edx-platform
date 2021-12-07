"""
All views for custom settings app
"""
import logging

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import ugettext as _
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import View
from milestones import api as milestones_api
from opaque_keys.edx.keys import CourseKey

from cms.djangoapps.contentstore.views.course import get_course_and_check_access
from lms.djangoapps.course_api.blocks.api import get_blocks
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.lib.gating.api import delete_prerequisites
from openedx.features.pakx.lms.overrides.utils import get_or_create_course_overview_content
from util.views import ensure_valid_course_key
from xmodule.modulestore.django import modulestore

from .models import CourseOverviewContent, CourseSet

log = logging.getLogger(__name__)


@method_decorator(ensure_csrf_cookie, name='dispatch')
@method_decorator(ensure_valid_course_key, name='dispatch')
class CourseCustomSettingsView(LoginRequiredMixin, View):
    """
    A view for PakistanX specific custom settings for a course
    """
    template_name = 'custom_settings.html'

    def get(self, request, course_key_string):
        """
        Show course custom settings page with course overview content editor
        """
        course_key = CourseKey.from_string(course_key_string)
        context_course = get_course_and_check_access(course_key, request.user)
        course_sets = CourseSet.objects.filter(
            publisher_org__organizationcourse__course_id=course_key, is_active=True
        ).only(
            'id', 'name'
        )

        course_overview_url = u'{overview_base_url}/courses/{course_key}/overview'.format(
            overview_base_url=configuration_helpers.get_value('LMS_ROOT_URL', settings.LMS_ROOT_URL),
            course_key=course_key,
        )
        course_overview_content = get_or_create_course_overview_content(course_key)

        subsections = self._get_chapters_and_subsections(course_key, request)
        log.info("\n\npre-reqs after filtering:{}".format(subsections))

        context = {
            'course_sets': course_sets,
            'context_course': context_course,
            'overview_content': course_overview_content,
            'course_overview_url': course_overview_url,
            'custom_settings_url': reverse('custom_settings', kwargs={'course_key_string': course_key}),
            'subsections': subsections,
            'days': course_overview_content.days_to_unlock,
            'selected_subsection': course_overview_content.subsection_to_lock
        }
        return render(request, self.template_name, context=context)

    def post(self, request, course_key_string):
        """
        Save course overview content in model and display updated version of custom settings page
        """
        course_key = CourseKey.from_string(course_key_string)

        course_set = request.POST['course-set']
        publisher_name = request.POST['publisher_name']
        course_overview = request.POST['course-overview']
        card_description = request.POST['card-description']
        is_public = request.POST.get('is_public', 'off') == 'on'
        course_experience = request.POST.get('course_experience', 0)
        publisher_logo_url = request.POST['publisher-logo-url']
        publisher_card_logo_url = request.POST['publisher_card_logo_url']
        days_to_unlock = int(request.POST.get('days-duration') or 0)
        subsection_to_lock = request.POST.get('subsection')

        self._add_days_milestone(subsection_to_lock, course_key)

        if course_overview is not None:
            CourseOverviewContent.objects.update_or_create(
                course_id=course_key,
                defaults={
                    'is_public': is_public,
                    'course_set_id': course_set,
                    'body_html': course_overview,
                    'publisher_name': publisher_name,
                    'card_description': card_description,
                    'course_experience': course_experience,
                    'publisher_logo_url': publisher_logo_url,
                    'publisher_card_logo_url': publisher_card_logo_url,
                    'days_to_unlock': days_to_unlock if subsection_to_lock else 0,
                    'subsection_to_lock': subsection_to_lock
                }
            )

        return redirect(reverse('custom_settings', kwargs={'course_key_string': course_key}))

    def _get_chapters_and_subsections(self, course_key, request):
        """Get all chapters and subsections for a course."""

        course_usage_key = modulestore().make_course_usage_key(course_key)
        block_types_filter = ['sequential']
        all_blocks = get_blocks(
            request,
            course_usage_key,
            user=request.user,
            nav_depth=3,
            requested_fields=['children', 'display_name', 'show_gated_sections', 'gated'],
            block_types_filter=block_types_filter,
            allow_start_dates_in_future=False,
        )
        pre_reqs = self._get_formatted_pre_reqs(course_key)
        return [x for x in all_blocks['blocks'].values() if 'gated' in x.keys() and x['id'] not in pre_reqs]

    @staticmethod
    def _get_formatted_pre_reqs(course_key):
        """Return list of ids of pre requisite subsections that are not pre req of other subsections."""

        pre_reqs = milestones_api.get_course_content_milestones(course_key=course_key, relationship='requires')

        log.info("\n\npre-reqs before format:{}".format(pre_reqs))

        formatted_pre_reqs = []
        for pre_req in pre_reqs:
            namespace = pre_req['namespace'].split('.')[0]
            if pre_req['content_id'] != namespace:
                formatted_pre_reqs.append(namespace)

        log.info("\n\npre-reqs after format:{}".format(formatted_pre_reqs))

        return formatted_pre_reqs

    def _add_days_milestone(self, subsection, course_key):
        """Update course milestones if subsection to lock has been changed."""

        course_overview_content = CourseOverviewContent.objects.get(course_id=course_key)

        if course_overview_content.subsection_to_lock != subsection:
            self._update_course_milestone(course_overview_content.subsection_to_lock, subsection, course_key)

    def _update_course_milestone(self, old_subsection, new_subsection, course_key):
        """Remove old milestone and add new milestone to lock specified subsection."""

        if old_subsection:
            delete_prerequisites(old_subsection, 'requires')

        if new_subsection:
            self._create_milestone(new_subsection, course_key)

    @staticmethod
    def _create_milestone(subsection, course_key):
        """Create a new milestone of a subsection that locks itself."""

        milestone = milestones_api.add_milestone(
            {
                'name': _(u'Gating milestone for {usage_key}').format(usage_key=str(subsection)),
                'namespace': "{usage_key}.gating".format(usage_key=subsection),
                'description': _('System defined milestone'),
            },
            propagate=False
        )
        milestones_api.add_course_content_milestone(course_key, subsection, 'requires', milestone)
