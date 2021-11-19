""" Overridden views from core """
from datetime import datetime

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import AnonymousUser
from django.forms.models import model_to_dict
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import ugettext as _
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic.base import TemplateView
from opaque_keys.edx.keys import CourseKey
from pytz import utc
from six import text_type
from waffle import switch_is_active

from course_modes.models import CourseMode, get_course_prices
from edxmako.shortcuts import marketing_link, render_to_response
from lms.djangoapps.commerce.utils import EcommerceService
from lms.djangoapps.courseware.access import has_access
from lms.djangoapps.courseware.access_utils import check_public_access
from lms.djangoapps.courseware.courses import (
    can_self_enroll_in_course,
    get_course_with_access,
    get_permission_for_course_about,
    get_studio_url
)
from lms.djangoapps.courseware.permissions import VIEW_COURSE_HOME, VIEW_COURSEWARE
from lms.djangoapps.courseware.views.index import render_accordion
from lms.djangoapps.courseware.views.views import _course_home_redirect_enabled, registered_for_course
from lms.djangoapps.instructor.enrollment import uses_shib
from openedx.core.djangoapps.catalog.utils import get_programs_with_type
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.enrollments.permissions import ENROLL_IN_COURSE
from openedx.core.djangoapps.models.course_details import CourseDetails
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangoapps.theming import helpers as theming_helpers
from openedx.features.course_experience import course_home_url_name
from openedx.features.course_experience.utils import get_course_outline_block_tree
from openedx.features.course_experience.waffle import ENABLE_COURSE_ABOUT_SIDEBAR_HTML
from openedx.features.course_experience.waffle import waffle as course_experience_waffle
from openedx.features.pakx.cms.custom_settings.models import CourseOverviewContent
from openedx.features.pakx.lms.overrides.forms import AboutUsForm
from openedx.features.pakx.lms.overrides.tasks import send_contact_us_email
from openedx.features.pakx.lms.overrides.utils import (
    add_course_progress_to_enrolled_courses,
    get_course_card_data,
    get_course_first_unit_lms_url,
    get_course_progress_percentage,
    get_courses_for_user,
    get_featured_course_data,
    get_featured_course_set,
    get_rating_classes_for_course,
    get_resume_course_info,
    is_course_enroll_able
)
from student.models import CourseEnrollment
from util.cache import cache_if_anonymous
from util.milestones_helpers import get_prerequisite_courses_display
from util.views import ensure_valid_course_key
from xmodule.course_module import COURSE_VISIBILITY_PUBLIC, COURSE_VISIBILITY_PUBLIC_OUTLINE
from xmodule.modulestore.django import modulestore


# NOTE: This view is not linked to directly--it is called from
# branding/views.py:index(), which is cached for anonymous users.
# This means that it should always return the same thing for anon
# users. (in particular, no switching based on query params allowed)
def index(request, extra_context=None, user=AnonymousUser()):
    """
    Render the edX main page.

    extra_context is used to allow immediate display of certain modal windows, eg signup.
    """
    if extra_context is None:
        extra_context = {}

    context = {
        'courses': get_courses_for_user(user),
        'featured_course': get_featured_course_data(),
        'featured_course_set': get_featured_course_set(),
        'show_partners': configuration_helpers.get_value('show_partners', True),
        'homepage_overlay_html': configuration_helpers.get_value('homepage_overlay_html'),
        'show_homepage_promo_video': configuration_helpers.get_value('show_homepage_promo_video', False),
        'homepage_course_max': configuration_helpers.get_value('HOMEPAGE_COURSE_MAX', settings.HOMEPAGE_COURSE_MAX)
    }

    # This appears to be an unused context parameter, at least for the master templates...

    # TO DISPLAY A YOUTUBE WELCOME VIDEO
    # 1) Change False to True

    # Maximum number of courses to display on the homepage.

    # 2) Add your video's YouTube ID (11 chars, eg "123456789xX"), or specify via site configuration
    # Note: This value should be moved into a configuration setting and plumbed-through to the
    # context via the site configuration workflow, versus living here
    youtube_video_id = configuration_helpers.get_value('homepage_promo_video_youtube_id', "your-youtube-id")
    context['homepage_promo_video_youtube_id'] = youtube_video_id

    # allow for theme override of the courses list
    context['courses_list'] = theming_helpers.get_template_path('courses_list.html')

    # Insert additional context for use in the template
    context.update(extra_context)

    # Add marketable programs to the context.
    context['programs_list'] = get_programs_with_type(request.site, include_hidden=False)

    return render_to_response('index.html', context)


@ensure_csrf_cookie
@login_required
def courses(request, section='in-progress'):
    """
    Render "find courses" page. The course selection work is done in courseware.courses.

    If the marketing site is enabled, redirect to that. Otherwise, if subdomain
    branding is on, this is the university profile page. Otherwise, it's the edX
    courseware.views.views.courses page

    Arguments:
          request (WSGIRequest): HTTP request object
          section (str): 'in-progress'/'upcoming'/'complete'
    """
    enable_mktg_site = configuration_helpers.get_value(
        'ENABLE_MKTG_SITE',
        settings.FEATURES.get('ENABLE_MKTG_SITE', False)
    )

    if enable_mktg_site:
        return redirect(marketing_link('COURSES'), permanent=True)

    if not settings.FEATURES.get('COURSES_ARE_BROWSABLE'):
        raise Http404

    #  we do not expect this case to be reached in cases where
    #  marketing is enabled or the courses are not browsable
    courses_list = []
    course_discovery_meanings = getattr(settings, 'COURSE_DISCOVERY_MEANINGS', {})
    if not settings.FEATURES.get('ENABLE_COURSE_DISCOVERY'):
        courses_list = get_courses_for_user(request.user)

    # split courses into categories i.e upcoming & in-progress
    in_progress_courses = []
    upcoming_courses = []
    completed_courses = []
    browse_courses = []

    add_course_progress_to_enrolled_courses(request, courses_list)
    show_only_enrolled_courses = switch_is_active('show_only_enrolled_courses')

    for course in courses_list:
        if not course.enrolled and hasattr(course, 'custom_settings') and course.custom_settings.is_public:
            browse_courses.append(course)
            continue
        if show_only_enrolled_courses and not course.enrolled:
            continue
        if course.user_progress == '100':
            completed_courses.append(course)
        elif course.has_started():
            in_progress_courses.append(course)
        else:
            upcoming_courses.append(course)

    # Add marketable programs to the context.
    programs_list = get_programs_with_type(request.site, include_hidden=False)
    return render_to_response(
        "courseware/courses.html",
        {
            'in_progress_courses': in_progress_courses,
            'upcoming_courses': upcoming_courses,
            'browse_courses': browse_courses,
            'completed_courses': completed_courses,
            'course_discovery_meanings': course_discovery_meanings,
            'programs_list': programs_list,
            'section': section,
            'show_only_enrolled_courses': show_only_enrolled_courses
        }
    )


@ensure_csrf_cookie
@login_required
def overview_tab_view(request, course_id=None):
    course_key = CourseKey.from_string(course_id)
    course = get_course_with_access(request.user, 'load', course_key)
    course_block_tree = get_course_outline_block_tree(
        request, text_type(course_id), request.user, allow_start_dates_in_future=True
    )
    course_experience_mode = "Normal"
    course_overview_content = CourseOverviewContent.objects.filter(course_id=course_key).first()
    if course_overview_content:
        course_experience_mode = course_overview_content.get_course_experience_display()

    context = {
        'course_overview': course_overview_content.body_html if course_overview_content else None,
        'user': request.user,
        'course': course,
        'accordion': render_accordion(request, course, course_block_tree, '', '',
                                      course_experience_mode=course_experience_mode)
    }
    return render_to_response('courseware/overview.html', context)


def _get_course_about_context(request, course_id, category=None):  # pylint: disable=too-many-statements
    """
    context required for course about page
    """

    course_key = CourseKey.from_string(course_id)

    # If a user is not able to enroll in a course then redirect
    # them away from the about page to the dashboard.
    if not can_self_enroll_in_course(course_key):
        return redirect(reverse('dashboard'))

    # If user needs to be redirected to course home then redirect
    if _course_home_redirect_enabled():
        return redirect(reverse(course_home_url_name(course_key), args=[text_type(course_key)]))

    with modulestore().bulk_operations(course_key):
        permission = get_permission_for_course_about()
        course = get_course_with_access(request.user, permission, course_key)
        course_details = CourseDetails.populate(course)
        modes = CourseMode.modes_for_course_dict(course_key)
        registered = registered_for_course(course, request.user)

        course_map = get_course_card_data(course)
        staff_access = bool(has_access(request.user, 'staff', course))
        studio_url = get_studio_url(course, 'settings/details')

        preview_course_url = None
        if not request.user.is_authenticated:
            course_block_tree = get_course_outline_block_tree(request, course_id, None)
            preview_course_url = get_course_first_unit_lms_url(course_block_tree)

        if request.user.has_perm(VIEW_COURSE_HOME, course):
            course_target = reverse(course_home_url_name(course.id), args=[text_type(course.id)])
        else:
            course_target = reverse('about_course', args=[text_type(course.id)])

        show_courseware_link = bool((request.user.has_perm(VIEW_COURSEWARE, course))
                                    or settings.FEATURES.get('ENABLE_LMS_MIGRATION'))

        # If the ecommerce checkout flow is enabled and the mode of the course is
        # professional or no id professional, we construct links for the enrollment
        # button to add the course to the ecommerce basket.
        ecomm_service = EcommerceService()
        ecommerce_checkout = ecomm_service.is_enabled(request.user)
        ecommerce_checkout_link = ''
        ecommerce_bulk_checkout_link = ''
        single_paid_mode = None
        if ecommerce_checkout:
            if len(modes) == 1 and list(modes.values())[0].min_price:
                single_paid_mode = list(modes.values())[0]
            else:
                # have professional ignore other modes for historical reasons
                single_paid_mode = modes.get(CourseMode.PROFESSIONAL)

            if single_paid_mode and single_paid_mode.sku:
                ecommerce_checkout_link = ecomm_service.get_checkout_page_url(single_paid_mode.sku)
            if single_paid_mode and single_paid_mode.bulk_sku:
                ecommerce_bulk_checkout_link = ecomm_service.get_checkout_page_url(single_paid_mode.bulk_sku)

        _, course_price = get_course_prices(course)

        # Used to provide context to message to student if enrollment not allowed
        can_enroll = bool(request.user.has_perm(ENROLL_IN_COURSE, course))
        can_enroll = can_enroll and is_course_enroll_able(course)

        invitation_only = course.invitation_only
        resume_course_url = None
        has_visited_course = None
        user_progress = 0
        if registered:
            has_visited_course, resume_course_url, _ = get_resume_course_info(request, course_id)
            user_progress = get_course_progress_percentage(request, course_id)
        is_course_full = CourseEnrollment.objects.is_course_full(course)

        # Register button should be disabled if one of the following is true:
        # - Student is already registered for course
        # - Course is already full
        # - Student cannot enroll in course
        active_reg_button = not (registered or is_course_full or not can_enroll)

        is_shib_course = uses_shib(course)
        language = dict(settings.ALL_LANGUAGES).get(course.language)

        # get prerequisite courses display names
        pre_requisite_courses = get_prerequisite_courses_display(course)

        # Overview
        overview = CourseOverview.get_from_id(course.id)

        starts_in = bool(overview.start_date and overview.start_date > datetime.now(utc))
        starts_in = starts_in and overview.start_date.strftime('%B %d, %Y')

        sidebar_html_enabled = course_experience_waffle().is_enabled(ENABLE_COURSE_ABOUT_SIDEBAR_HTML)

        allow_anonymous = check_public_access(course, [COURSE_VISIBILITY_PUBLIC, COURSE_VISIBILITY_PUBLIC_OUTLINE])

        # This local import is due to the circularity of lms and openedx references.
        # This may be resolved by using stevedore to allow web fragments to be used
        # as plugins, and to avoid the direct import.
        from openedx.features.course_experience.views.course_reviews import CourseReviewsModuleFragmentView

        # Embed the course reviews tool
        reviews_fragment_view = CourseReviewsModuleFragmentView().render_to_fragment(request, course=course)

        context = {
            'course': course,
            'language': language,
            'preview_course_url': preview_course_url,
            'course_details': course_details,
            'staff_access': staff_access,
            'studio_url': studio_url,
            'registered': registered,
            'course_target': course_target,
            'is_cosmetic_price_enabled': settings.FEATURES.get('ENABLE_COSMETIC_DISPLAY_PRICE'),
            'course_price': course_price,
            'ecommerce_checkout': ecommerce_checkout,
            'ecommerce_checkout_link': ecommerce_checkout_link,
            'ecommerce_bulk_checkout_link': ecommerce_bulk_checkout_link,
            'single_paid_mode': single_paid_mode,
            'show_courseware_link': show_courseware_link,
            'is_course_full': is_course_full,
            'can_enroll': can_enroll,
            'invitation_only': invitation_only,
            'active_reg_button': active_reg_button,
            'is_shib_course': is_shib_course,
            # We do not want to display the internal courseware header, which is used when the course is found in the
            # context. This value is therefor explicitly set to render the appropriate header.
            'disable_courseware_header': True,
            'pre_requisite_courses': pre_requisite_courses,
            'course_image_urls': overview.image_urls,
            'reviews_fragment_view': reviews_fragment_view,
            'sidebar_html_enabled': sidebar_html_enabled,
            'allow_anonymous': allow_anonymous,
            'category': category,
            'resume_course_url': resume_course_url,
            'has_visited_course': has_visited_course,
            'user_progress': user_progress,
            'org_name': course_map['org_name'],
            'org_short_logo': course_map['org_logo_url'],
            'starts_in': starts_in,
            'org_description': course_map['org_description'],
            'publisher_logo': course_map['publisher_logo_url'],
            'course_rating': get_rating_classes_for_course(course_id)
        }

        return context


@ensure_csrf_cookie
@ensure_valid_course_key
@cache_if_anonymous()
def course_about(request, course_id):
    """
    Display the course's about page.
    """

    return render_to_response('courseware/course_about.html', _get_course_about_context(request, course_id))


# noinspection PyInterpreter
@ensure_csrf_cookie
@ensure_valid_course_key
@cache_if_anonymous()
def course_about_category(request, category, course_id):
    """
    Display the course's about page.

    Arguments:
        request (WSGIRequest): HTTP request
        course_id (str): Unique ID of course
        category (str): 'In Progress'/'Upcoming'/'Completed'
    """

    return render_to_response('courseware/course_about.html', _get_course_about_context(request, course_id, category))


class AboutUsView(TemplateView):
    """
    View for viewing and submitting contact us form.
    """

    form_class = AboutUsForm
    success_redirect = '/about_us/'
    template_name = 'overrides/about_us.html'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.email_subject = 'Contact Us Form Data'
        self.initial_data = {}

    def populate_form_initial_data(self, user=None):
        if user:
            self.initial_data.update({
                'email': user.email,
                'full_name': (user.profile.name or user.get_full_name()).title().strip(),
                'organization': getattr(user.profile.organization, 'name', ''),
            })

    def get_context_data(self, user=None, **kwargs):  # pylint: disable=arguments-differ
        context = super().get_context_data(**kwargs)
        context['tags'] = ['LMS']
        context['platform_name'] = configuration_helpers.get_value('platform_name', settings.PLATFORM_NAME)
        context['support_email'] = configuration_helpers.get_value('CONTACT_EMAIL', settings.CONTACT_EMAIL)
        context['custom_fields'] = settings.ZENDESK_CUSTOM_FIELDS

        self.populate_form_initial_data(user)
        context['form'] = self.form_class(initial=self.initial_data)
        return context

    def get(self, request):  # pylint: disable=arguments-differ
        user = request.user if request.user.is_authenticated else None
        context = self.get_context_data(user=user)

        context['course_id'] = request.session.get('course_id', '')

        return render_to_response(self.template_name, context)

    def post(self, request):
        form_data = request.POST.copy()
        form = self.form_class(form_data)
        if form.is_valid():
            instance = form.save(commit=False)
            if request.user.is_authenticated:
                instance.created_by = request.user
            instance.save()

            email_data = model_to_dict(
                instance, fields=['full_name', 'email', 'organization', 'phone', 'message']
            )
            email_data['subject'] = self.email_subject
            email_data['form_message'] = email_data.pop('message')
            send_contact_us_email(email_data)

            messages.success(
                self.request,
                _(u'Thank you for contacting us! Our team will get in touch with you soon')
            )
            return HttpResponseRedirect(self.success_redirect)

        context = self.get_context_data()
        context['form'] = form
        return render_to_response(self.template_name, context)


class PartnerWithUsView(AboutUsView):
    """
    View for partner-with-us page.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.email_subject = 'Partner with Us Form Data'

    success_redirect = '/partner-with-us/'
    template_name = "overrides/partner_with_us.html"


class BusinessView(AboutUsView):
    """
    View for business page.
    """
    template_name = 'overrides/business.html'
    success_redirect = '/business/#get-started'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.email_subject = 'PakistanX for Business Form Data'

    def populate_form_initial_data(self, user=None):
        super().populate_form_initial_data(user)
        self.initial_data.update({'message': 'Not Available. Submitted from Business Page'})


class MarketingCampaignPage(AboutUsView):
    """
    View for business page.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.email_subject = 'Pakistan Against Workplace Harassment Form Data'

    template_name = 'overrides/marketing_campaign.html'
    success_redirect = '/workplace-harassment/#get-started'

    def populate_form_initial_data(self, user=None):
        super().populate_form_initial_data(user)
        self.initial_data.update({'message': 'Not Available. Submitted from Marketing campaign Page'})


class WEShowcaseView(AboutUsView):
    """
    View for business page.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.email_subject = 'Workplace Essential Demo Page Form Data'

    template_name = 'overrides/workplace_essential_showcase.html'
    success_redirect = '/workplace-essentials-showcase/#get-started'

    def get_context_data(self, user=None, **kwargs):  # pylint: disable=arguments-differ
        course_keys = configuration_helpers.get_value('we_demo_course_keys') or []
        context = super().get_context_data(user=None, **kwargs)
        course_url_map = {}

        for idx, course_key in enumerate(course_keys):
            course_block_tree = get_course_outline_block_tree(self.request, course_key, None)
            course_url_map[str(idx + 1)] = {
                'about_url': '/courses/{}/about'.format(course_key),
                'preview_url': get_course_first_unit_lms_url(course_block_tree)
            }

        context['course_url_map'] = course_url_map
        return context

    def populate_form_initial_data(self, user=None):
        super().populate_form_initial_data(user)
        self.initial_data.update({'message': 'Not Available. Submitted from WE Demo Page'})
