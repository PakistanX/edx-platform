"""
All views for custom settings app
"""
import logging
from datetime import datetime

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.core.management import call_command
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import ugettext as _
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import TemplateView, View
from milestones import api as milestones_api
from milestones import models as internal
from opaque_keys.edx.keys import CourseKey
from six import text_type
from slumber.exceptions import HttpClientError, HttpServerError

from cms.djangoapps.contentstore.views.course import get_course_and_check_access
from lms.djangoapps.course_api.blocks.api import get_blocks
from openedx.core.djangoapps.catalog.cache import PROGRAM_CACHE_KEY_TPL
from openedx.core.djangoapps.catalog.utils import (
    create_catalog_api_client,
    get_programs
)
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.lib.gating.api import delete_prerequisites
from openedx.features.pakx.common.utils import truncate_string_up_to, get_program
from openedx.features.pakx.lms.overrides.utils import get_or_create_course_overview_content
from util.views import ensure_valid_course_key
from xmodule.modulestore.django import modulestore

from .models import CourseOverviewContent, CourseSet, ProgramCustomData

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
            'selected_subsection': course_overview_content.subsection_to_lock,
            'email_days': course_overview_content.days_till_next_reminder,
            'email_deadline': course_overview_content.reminder_stop_date,
        }
        return render(request, self.template_name, context=context)

    def post(self, request, course_key_string):
        """
        Save course overview content in model and display updated version of custom settings page
        """
        course_key = CourseKey.from_string(course_key_string)

        course_set = request.POST['course-set']
        publisher_name = truncate_string_up_to(request.POST['publisher_name'], 128)
        publisher_description = request.POST['publisher_description']
        course_overview = request.POST['course-overview']
        course_for_you_html = request.POST['course-for-you']
        faq_html = request.POST['faq']
        instructors_html = request.POST['course-instructors']
        certificate_html = request.POST['course-certificate']
        offer_by_html = request.POST['offered-by']
        reviews = request.POST['reviews']
        card_description = truncate_string_up_to(request.POST['card-description'], 256)
        is_public = request.POST.get('is_public', 'off') == 'on'
        course_experience = request.POST.get('course_experience', 0)
        publisher_logo_url = truncate_string_up_to(request.POST['publisher-logo-url'], 256)
        group_enrollment_url = truncate_string_up_to(request.POST['group_enrollment_url'], 256)
        publisher_card_logo_url = truncate_string_up_to(request.POST['publisher_card_logo_url'], 256)
        course_banner_image_url = truncate_string_up_to(request.POST['course_banner_image_url'], 256)
        about_page_image_url = truncate_string_up_to(request.POST['about_page_image_url'], 256)
        days_to_unlock = int(request.POST.get('days-duration') or 0)
        subsection_to_lock = request.POST.get('subsection')
        email_days, email_deadline = self._clean_email_reminder_data(request)

        self._add_days_milestone(subsection_to_lock, course_key)

        if course_overview is not None:
            CourseOverviewContent.objects.update_or_create(
                course_id=course_key,
                defaults={
                    'is_public': is_public,
                    'course_set_id': course_set,
                    'body_html': course_overview,
                    'course_for_you_html': course_for_you_html,
                    'instructors_html': instructors_html,
                    'certificate_html': certificate_html,
                    'offered_by_html': offer_by_html,
                    'reviews_html': reviews,
                    'faq_html': faq_html,
                    'publisher_name': publisher_name,
                    'publisher_description': publisher_description,
                    'card_description': card_description,
                    'course_experience': course_experience,
                    'publisher_logo_url': publisher_logo_url,
                    'group_enrollment_url': group_enrollment_url,
                    'course_banner_image_url': course_banner_image_url,
                    'publisher_card_logo_url': publisher_card_logo_url,
                    'days_to_unlock': days_to_unlock if subsection_to_lock else 0,
                    'subsection_to_lock': subsection_to_lock,
                    'days_till_next_reminder': email_days,
                    'reminder_stop_date': email_deadline,
                    'about_page_image_url': about_page_image_url,
                }
            )

        return redirect(reverse('custom_settings', kwargs={'course_key_string': course_key}))

    @staticmethod
    def _clean_email_reminder_data(request):
        """Clean data needed for reminder emails."""

        email_days = int(request.POST.get('email-days') or 0)
        email_deadline = request.POST.get('email-deadline')
        email_deadline = datetime.strptime(email_deadline, '%Y-%m-%d') if email_deadline else None
        return email_days, email_deadline

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

    def _get_formatted_pre_reqs(self, course_key):
        """Return list of ids of pre requisite subsections that are not pre req of other subsections."""

        pre_reqs = self._get_course_content_milestones(course_key)

        log.info("\n\npre-reqs before format:{}".format(pre_reqs))

        formatted_pre_reqs = set()
        for pre_req in pre_reqs:
            namespace = pre_req['namespace'].split('.')[0]
            if pre_req['relationship'] == 'fulfills' or pre_req['content_id'] != namespace:
                formatted_pre_reqs.add(namespace)

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

    def _get_course_content_milestones(self, course_key):
        """Get a list of CourseContentMilestones based on course_key."""

        queryset = internal.CourseContentMilestone.objects.filter(
            active=True,
            course_id=text_type(course_key)
        ).select_related('milestone')

        return [self._serialize_milestone_with_course_content(ccm) for ccm in queryset]

    @staticmethod
    def _serialize_milestone_with_course_content(course_content_milestone):
        """CourseContentMilestone serialization (composite object)."""

        return {
            'id': course_content_milestone.milestone.id,
            'name': course_content_milestone.milestone.name,
            'display_name': course_content_milestone.milestone.display_name,
            'namespace': course_content_milestone.milestone.namespace,
            'description': course_content_milestone.milestone.description,
            'course_id': course_content_milestone.course_id,
            'content_id': course_content_milestone.content_id,
            'relationship': course_content_milestone.milestone_relationship_type.name
        }


@method_decorator(ensure_csrf_cookie, name='dispatch')
class ListProgramsView(LoginRequiredMixin, TemplateView):
    """Show a list of all programs."""

    template_name = 'programs/programs.html'

    def get_context_data(self, **kwargs):
        """Add list of programs in context."""
        context = super(ListProgramsView, self).get_context_data(**kwargs)

        programs = get_programs(site=Site.objects.get_current())
        if not programs:
            call_command('cache_programs')
            programs = get_programs(site=Site.objects.get_current())

        context['programs'] = programs
        return context


@method_decorator(ensure_csrf_cookie, name='dispatch')
class ProgramMixin(LoginRequiredMixin, View):
    """Mixin for updating or creating programs."""

    template_name = 'programs/program.html'
    errors = {
        '500': 'An error occurred on discovery server while saving the information',
        '400': 'Invalid data format for any one of the following: Courses, Publisher Logo URL or Marketing Slug'
    }

    def extract_api_data(self):
        return {
            'title': self.request.POST['program_title'],
            'overview': self.request.POST['overview'],
            'courses': self.request.POST['courses'].split(','),
            'card_image_url': truncate_string_up_to(self.request.POST['card_image_url'], 255),
            'marketing_slug': truncate_string_up_to(self.request.POST['marketing_slug'], 255),
            'total_hours_of_effort': self.request.POST['total_hours_of_effort'],
        }

    def extract_custom_program_data(self):
        return {
            'program_for_you_html': self.request.POST['program_for_you_html'],
            'instructors_html': self.request.POST['instructors_html'],
            'certificate_html': self.request.POST['certificate_html'],
            'offered_by_html': self.request.POST['offered_by_html'],
            'reviews_html': self.request.POST['reviews_html'],
            'faq_html': self.request.POST['faq_html'],
            'image_url': self.request.POST['image_url'],
            'group_enrollment_url': self.request.POST['group_enrollment_url'],
            'video_url': self.request.POST['video_url'],
        }

    def update_discover_program(self, program_uuid=None):
        """Update program in discovery.."""
        data = self.extract_api_data()
        client = create_catalog_api_client(self.request.user, site=Site.objects.get_current())
        program = self.get_data_from_api(data, client, program_uuid=program_uuid)
        program_uuid = program['uuid']
        cache.set(PROGRAM_CACHE_KEY_TPL.format(uuid=program_uuid), program)
        return program_uuid

    def return_error(self, message, program_uuid=None):
        """Return Response for errors."""
        context = self.parse_program_data(self.get_all_program_data(program_uuid=program_uuid))
        context.update({'error': True, 'error_msg': message})
        return render(self.request, self.template_name, context=context)

    def update_program_html(self, program_uuid):
        """Update program in CMS."""
        ProgramCustomData.objects.update_or_create(
            program_uuid=program_uuid,
            defaults=self.extract_custom_program_data()
        )


class EditProgramView(ProgramMixin):
    """Edit a program."""

    @staticmethod
    def parse_program_data(program):
        """Parse program data to our format."""
        course_ids = [course['course_runs'][0]['key'] for course in program['courses']]
        program.update({'program_title': program['title'], 'courses': ','.join(course_ids)})
        return program

    def get_all_program_data(self, program_uuid=None):
        """Get program data from cache and database."""
        cache_program = get_program(program_uuid)
        program, _ = ProgramCustomData.objects.get_or_create(program_uuid=program_uuid)
        cache_program.update(program.__dict__)
        return cache_program

    @staticmethod
    def get_data_from_api(data, client, program_uuid=None):
        """Patch API call for updating program."""
        return client.program(program_uuid).patch(data)

    def get(self, request, program_uuid):
        program = self.get_all_program_data(program_uuid)
        return render(request, self.template_name, context=self.parse_program_data(program))

    def post(self, request, program_uuid):
        try:
            self.update_discover_program(program_uuid)
        except HttpServerError:
            return self.return_error(
                self.errors['500'],
                program_uuid=program_uuid
            )
        except HttpClientError:
            return self.return_error(
                self.errors['400'],
                program_uuid=program_uuid
            )
        self.update_program_html(program_uuid)
        return redirect(reverse('edit-program-cms', kwargs={'program_uuid': program_uuid}))


class ProgramCreateView(ProgramMixin):
    """Create a program."""

    @staticmethod
    def get_data_from_api(data, client, program_uuid=None):
        """Patch API call for updating program."""
        return client.program.post(data)

    def get_all_program_data(self, program_uuid=None):
        """Get program data from cache and database."""
        context = self.extract_api_data()
        context['courses'] = self.request.POST['courses']
        context.update(self.extract_custom_program_data())
        return context

    @staticmethod
    def parse_program_data(program):
        """Parse program data to our format."""
        program.update({'program_title': program['title']})
        return program

    def get(self, request):
        """List down fields required for creating a program."""
        return render(request, self.template_name)

    def post(self, request):
        try:
            program_uuid = self.update_discover_program()
        except HttpServerError:
            return self.return_error(self.errors['500'])
        except HttpClientError:
            return self.return_error(self.errors['400'])
        self.update_program_html(program_uuid)
        return redirect(reverse('edit-program-cms', kwargs={'program_uuid': program_uuid}))
