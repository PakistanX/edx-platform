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
from openedx.core.lib.gating.api import remove_prerequisite
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
        context_course = get_course_and_check_access(course_key, request.user, depth=2)
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

        chapters = self.get_chapters_and_subsections(course_key, request)

        context = {
            'course_sets': course_sets,
            'context_course': context_course,
            'overview_content': course_overview_content,
            'course_overview_url': course_overview_url,
            'custom_settings_url': reverse('custom_settings', kwargs={'course_key_string': course_key}),
            'chapters': [chapter for chapter in chapters.values() if 'gated' in chapter.keys()],
            'days': course_overview_content.days_to_unlock,
            'selected_chapter': course_overview_content.subsection_to_lock
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
        days_to_unlock = int(request.POST['days-duration'])
        subsection_to_lock = request.POST['subsection']

        self.add_days_milestone(subsection_to_lock, course_key)

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
                    'days_to_unlock': days_to_unlock,
                    'subsection_to_lock': subsection_to_lock
                }
            )

        return redirect(reverse('custom_settings', kwargs={'course_key_string': course_key}))

    def get_chapters_and_subsections(self, course, request):
        """Get all chapters and subsections for a course."""

        course_usage_key = modulestore().make_course_usage_key(course)
        block_types_filter = ['sequential']
        all_blocks = get_blocks(
            request,
            course_usage_key,
            user=request.user,
            nav_depth=3,
            requested_fields=[
                'children',
                'display_name',
                'show_gated_sections',
                'gated',
            ],
            block_types_filter=block_types_filter,
            allow_start_dates_in_future=False,
        )
        return all_blocks['blocks']

    def add_days_milestone(self, subsection, course_key):
        """Update course milestones if subsection to lock has been changed."""

        course_overview_content = CourseOverviewContent.objects.get(course_id=course_key)

        if subsection and course_overview_content.subsection_to_lock != subsection:
            self.update_course_milestone(course_overview_content.subsection_to_lock, subsection, course_key)

    def update_course_milestone(self, old_subsection, new_subsection, course_key):
        """Remove old milestone and add new milestone to lock specified subsection."""

        if old_subsection:
            remove_prerequisite(old_subsection)

        self.create_milestone(new_subsection, course_key)

    def create_milestone(self, subsection, course_key):
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
