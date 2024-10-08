""" Overrides app util functions """

from collections import OrderedDict
from datetime import date, datetime
from logging import getLogger
from re import compile as re_compile
from re import findall

from completion.models import BlockCompletion
from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import MinLengthValidator, RegexValidator
from django.db.models import Avg, Case, Count, IntegerField, Sum, When
from django.db.models.functions import Coalesce
from django.test import RequestFactory
from django.urls import reverse
from django.urls.exceptions import NoReverseMatch
from django.utils.translation import ugettext as _
from opaque_keys.edx.keys import CourseKey
from pytz import utc
from six import text_type

from course_modes.models import CourseMode, format_course_price
from lms.djangoapps.commerce.utils import EcommerceService
from lms.djangoapps.course_api.blocks.serializers import BlockDictSerializer
from lms.djangoapps.course_api.blocks.transformers.blocks_api import BlocksAPITransformer
from lms.djangoapps.courseware.courses import get_courses, sort_by_announcement, sort_by_start_date
from lms.djangoapps.courseware.model_data import FieldDataCache
from lms.djangoapps.courseware.module_render import toc_for_course
from openedx.core.djangoapps.content.block_structure.api import get_course_in_cache
from openedx.core.djangoapps.content.block_structure.transformers import BlockStructureTransformers
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.crawlers.models import CrawlersConfig
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.lib.request_utils import get_request_or_stub
from openedx.features.course_experience.utils import get_course_outline_block_tree, get_resume_block
from openedx.features.pakx.cms.custom_settings.models import CourseOverviewContent
from pakx_feedback.feedback_app.models import UserFeedbackModel
from student.models import CourseEnrollment
from util.organizations_helpers import get_organization_by_short_name
from xmodule import course_metadata_utils

log = getLogger(__name__)

VIDEO_BLOCK_TYPES = ['video', 'pakx_video']
CORE_BLOCK_TYPES = ['html', 'video', 'problem', 'pakx_video', 'pakx_video_quiz',
                    'edly_carousel', 'pakx_grid_dropdown', 'openassessment', ]
PROBLEM_BLOCK_TYPES = ['problem', 'edly_carousel', 'pakx_grid_dropdown', 'pakx_video_quiz']
BLOCK_TYPES_TO_FILTER = [
    'course', 'chapter', 'sequential', 'vertical', 'discussion', 'openassessment', 'pb-mcq', 'pb-answer', 'pb-choice',
    'pb-message', 'pakx_microlearning', 'pakx_completion', 'google_drive', 'google-drive', 'google_document',
    'google-document'
]


def get_or_create_course_overview_content(course_key, custom_setting=None):
    course_overview_content, _ = CourseOverviewContent.objects.get_or_create(
        course_id=course_key, defaults=custom_setting or {}
    )
    return course_overview_content


def _get_org_log(organization):
    org_logo = organization.get('logo')
    return org_logo.url if org_logo else org_logo


def is_blank_str(value):
    return value is None or not value or value.isspace()


def get_course_card_data(course, org_prefetched=False):
    """
    Get course data required for home page course card

    :returns (dict): dict of course card data
    """
    if not isinstance(course, CourseOverview):
        course = CourseOverview.objects.filter(id=course.id).first()
    pakx_short_logo = '/static/pakx/images/mooc/pakx-logo.png'
    course_custom_setting = get_or_create_course_overview_content(course.id)

    if not org_prefetched:
        course_org = get_organization_by_short_name(course.org)
        org_description = course_org.get('description') if is_blank_str(course_custom_setting.publisher_description) \
            else course_custom_setting.publisher_description
        org_logo_url = course_custom_setting.publisher_card_logo_url or _get_org_log(course_org)
        org_name = course_org.get('name') if is_blank_str(course_custom_setting.publisher_name) else \
            course_custom_setting.publisher_name
    else:

        org_name = course_custom_setting.course_set.publisher_org.name if is_blank_str(
            course_custom_setting.publisher_name) else course_custom_setting.publisher_name
        org_logo_url = course_custom_setting.publisher_card_logo_url or course_custom_setting.\
            course_set.publisher_org.logo
        org_description = course_custom_setting.course_set.publisher_org.description if is_blank_str(
            course_custom_setting.publisher_description) else course_custom_setting.publisher_description

    try:
        program_name, program_id = course_custom_setting.program_detail.split('|')
        program_url = reverse('program_marketing_view', kwargs={'program_uuid': program_id})
    except ValueError:
        program_name = course_custom_setting.program_detail.split('|')
        program_url = ''
    except NoReverseMatch:
        program_url = program_id

    return {
        'key': course.id,
        'org_name': org_name,
        'effort': course.effort,
        'image': course.course_image_url,
        'org_description': org_description,
        'name': course.display_name_with_default,
        'org_logo_url': org_logo_url or pakx_short_logo,
        'short_description': course_custom_setting.card_description,
        'course_for_you': course_custom_setting.course_for_you_html,
        'group_enrollment_url': course_custom_setting.group_enrollment_url,
        'faq': course_custom_setting.faq_html,
        'instructors': course_custom_setting.instructors_html,
        'certificate': course_custom_setting.certificate_html,
        'offered_by': course_custom_setting.offered_by_html,
        'reviews': course_custom_setting.reviews_html,
        'publisher_logo_url': course_custom_setting.publisher_logo_url,
        'about_page_banner_color': course_custom_setting.about_page_banner_color,
        'is_text_color_dark': course_custom_setting.is_text_color_dark,
        'url': reverse('about_course', kwargs={'course_id': text_type(course.id)}),
        'enrollment_count': course_custom_setting.enrollment_count,
        'program_name': program_name,
        'program_url': program_url,
        'difficulty_level': course_custom_setting.difficulty_level,
        'discount_percent': course_custom_setting.discount_percent,
        'discount_date': course_custom_setting.discount_date,
        'seo_words': course_custom_setting.seo_words
    }


def get_featured_course_set():
    """
    Get featured course-set data in a list of dict
    :return (list): return list of dict containing course data
    """
    feature_course_set_name = configuration_helpers.get_value('feature_course_set_name')
    if not feature_course_set_name:
        return []

    courses = CourseOverview.objects.filter(
        custom_settings__course_set__name__iexact=feature_course_set_name
    ).prefetch_related(
        'custom_settings__course_set__publisher_org',
    )
    return [get_course_card_data(course, org_prefetched=True) for course in courses]


def get_active_campaign_data():
    """
    Get featured course, if active_campaign is set in Site Configurations
    :returns (CourseOverview): course or None
    """

    return configuration_helpers.get_value('active_campaign')


def get_rating_course(course_id):
    """
    Get course rating for given course ID
    :param course_id: (CourseKeyField) course key

    :return: (dict) {'rating': 4.3, 'total_responses': 34}
    """

    course_rating = UserFeedbackModel.objects.filter(
        course_id=course_id
    ).aggregate(
        rating=Avg('experience'), total_responses=Count('experience')
    )
    if course_rating.get("rating"):
        course_rating.update({"rating": round(float(course_rating.get("rating")) * 2.0) / 2.0})
    return course_rating


def get_rating_classes_for_course(course_id):
    """
    Get total number of responses and stars classes for given course ID
    This method is intended to be used in HTML templates to display star classes
    :param course_id: (CourseKeyField) course Key

    :return: (dict) {1:'fa-star', 2: 'fa-star', 3: 'fa-star', 4: 'fa-star-half-o', 5: 'fa-star-o'
                    'rating': 4.3, 'total_responses': 34}
    """

    full_star = 'fa-star'
    half_star = 'fa-star-half-o'
    class_dict = {1: 'fa-star-o', 2: 'fa-star-o', 3: 'fa-star-o', 4: 'fa-star-o', 5: 'fa-star-o'}  # Default empty stars
    rating_dict = get_rating_course(course_id)
    course_rating = rating_dict.get('rating')
    if course_rating is not None:
        for i, key in enumerate(class_dict):
            star_number = i + 1
            if int(course_rating) >= star_number:
                class_dict[key] = full_star
            elif (star_number - 0.5) == course_rating:
                class_dict[key] = half_star

    rating_dict.update(class_dict)
    return rating_dict


def get_courses_for_user(user):
    """
    get courses for given user object

    :returns [CourseOverview]: List of courses
    """

    courses = get_courses(user)
    if configuration_helpers.get_value(
        "ENABLE_COURSE_SORTING_BY_START_DATE",
        settings.FEATURES["ENABLE_COURSE_SORTING_BY_START_DATE"],
    ):
        courses = sort_by_start_date(courses)
    else:
        courses = sort_by_announcement(courses)
    return courses


def get_course_mode_and_content_class(course_overview):
    """
    get course mode and content class for given course overview
    :param course_overview: (CourseOverview) course overview object

    :return (str, str): tuple of string

    """
    custom_settings = get_or_create_course_overview_content(course_overview.id)
    course_experience_mode = custom_settings.get_course_experience_display()
    content_class = 'video-course-content' if course_experience_mode == 'Video' else ''
    return course_experience_mode, content_class


def get_resume_course_info(request, course_id, are_future_start_dates_allowed=False):
    """
    adds information relevant to resume course functionality to the given course model and progress

    Arguments:
    :param request: (HttpRequest) request object
    :param course_id: (str) course key
    :param are_future_start_dates_allowed: (bool) When True, will allow blocks to be
            returned that can bypass the StartDateTransformer's filter to show
            blocks with start dates in the future.

    Returns a tuple: (has_visited_course, resume_course_url, resume_course_title)
        has_visited_course: True if the user has ever visited the course, False otherwise.
        resume_course_url: The URL of the 'resume course' block if the user has visited the course,
                        otherwise the URL of the course root.
        resume_course_title: The display_name of the resume course block, otherwise the display_name of course root

    """
    course_id = text_type(course_id)
    course_outline_root_block = get_course_outline_block_tree(request, course_id, request.user,
                                                              are_future_start_dates_allowed)
    resume_block = get_resume_block(course_outline_root_block) if course_outline_root_block else None
    has_visited_course = bool(resume_block)
    if resume_block:
        resume_course_url = resume_block['lms_web_url']
        resume_course_title = resume_block['display_name']
    else:
        resume_course_url = course_outline_root_block['lms_web_url'] if course_outline_root_block else None
        resume_course_title = course_outline_root_block['display_name'] if course_outline_root_block else None

    return has_visited_course, resume_course_url, resume_course_title


def add_course_progress_to_enrolled_courses(request, courses_list):
    """
    Adds a tag enrolled to the course in which user is enrolled

    :param request: (HttpRequest) request object
    :param courses_list: [CourseView] list of course view objects
    """
    for course in courses_list:
        is_enrolled = CourseEnrollment.is_enrolled(request.user, course.id)
        course.user_progress = '0'
        if is_enrolled:
            course_id = text_type(course.id)
            has_visited_course, resume_course_url, resume_course_title = get_resume_course_info(
                request, course_id, True
            )
            course.user_progress = get_course_progress_percentage(request, course_id)
            course.resume_course_url = resume_course_url
            course.has_visited_course = has_visited_course
            course.resume_course_title = resume_course_title
        course.enrolled = is_enrolled
        course.dir = 'rtl' if is_rtl_language(course.language) else ''


def _accumulate_total_block_counts(total_block_type_counts):
    """
    Converts total_block_type_counts to required format.

    Accumulates all types of completable course blocks except html, problem and video
    into an 'other' category.
    :param total_block_type_counts: (dict) Total block type counts of required course
    :returns: accumulated_data (dict): Accumulated block type counts
    """

    accumulated_data = {
        'problem': 0,
        'video': 0,
        'html': 0,
        'other': 0
    }
    mapped_type = {  # pakx_video should be treated as Video & carousel as problem type
        'pakx_video': 'video',
        'edly_carousel': 'problem',
        'pakx_grid_dropdown': 'problem',
        'edly_assessment': 'problem',
        'pakx_assessment': 'problem',
        'pakx_video_quiz': 'problem',
        'openassessment': 'problem',
        'journal_xblock': 'other',
        'github_xblock': 'problem',
    }
    if total_block_type_counts:
        for block_type, count in total_block_type_counts.items():
            if block_type in CORE_BLOCK_TYPES:
                mapped_key = mapped_type.get(block_type, block_type)
                accumulated_data[mapped_key] = accumulated_data.get(mapped_key, 0) + count
            else:
                accumulated_data['other'] += count

    return accumulated_data


def _get_block_types_and_keys(course_block_structure):
    """
    Gets all completable course block types and block keys in the course block structure.

    Arguments:
        course_block_structure (CourseBlockStructure): CourseBlockStructure to get block types from

    Returns:
        block_types (list): list of block types in given course structure
        block_keys (list): list of block keys in given course structure
    """
    block_types = set()
    block_keys = set()
    for block_key in course_block_structure:
        block_type = course_block_structure.get_xblock_field(block_key, 'category')
        if block_type not in BLOCK_TYPES_TO_FILTER:
            block_types.add(block_type)
            block_keys.add(block_key)

    return block_types, block_keys


def _serialize_course_block_structure(request, course_block_structure):
    """
    Serializes course block structure into dict.

    Arguments:
        request (HttpReques): Request object for serializer context
        course_block_structure (CourseBlockStructure): Course block structure to serialize

    Returns:
        course_block_structure_serializer.data: Serialized course block structure
        block_keys (list): list of block keys in given course structure
    """

    block_types, block_keys = _get_block_types_and_keys(course_block_structure)
    transformers = BlockStructureTransformers()
    transformers += [
        BlocksAPITransformer(block_types_to_count=block_types, requested_student_view_data=set([]), depth=0)
    ]
    transformers.transform(course_block_structure)
    serializer_context = {
        'request': request,
        'block_structure': course_block_structure,
        'requested_fields': ['block_counts'],
    }
    course_block_structure_serializer = BlockDictSerializer(
        course_block_structure,
        context=serializer_context,
        many=False
    )

    return course_block_structure_serializer.data, block_keys


def create_dummy_request(site, user):
    """
    create a dummy request for a given site and user
    :param site: Site object
    :param user: User object

    :return: (WSGI Request) Dummy WSGI Request
    """

    request = get_request_or_stub()
    request.site = site
    request.user = user

    return request


def get_progress_information(request, course_key):
    course_key = CourseKey.from_string(course_key)
    course_block_structure = get_course_in_cache(course_key)
    serialized_course_block_structure, course_blocks_keys = _serialize_course_block_structure(
        request, course_block_structure)
    blocks = serialized_course_block_structure.get('blocks')
    total_block_types = _accumulate_total_block_counts(
        blocks.get(list(blocks.keys())[0]).get('block_counts')
    )
    total_blocks = sum(total_block_types.values())
    completions = BlockCompletion.objects.filter(user=request.user, context_key=course_key,
                                                 block_key__in=course_blocks_keys)
    total_completed_block_types = completions.aggregate(
        video=Coalesce(
            Sum(Case(When(block_type__in=VIDEO_BLOCK_TYPES, then=1), default=0, output_field=IntegerField())), 0
        ),
        problem=Coalesce(
            Sum(Case(When(block_type__in=PROBLEM_BLOCK_TYPES, then=1), default=0, output_field=IntegerField())), 0
        ),
        html=Coalesce(
            Sum(Case(When(block_type='html', then=1), default=0, output_field=IntegerField())), 0
        ),
        other=Coalesce(
            Sum(Case(When(block_type__in=CORE_BLOCK_TYPES, then=0), default=1, output_field=IntegerField())), 0
        ),
    )
    total_completed_blocks = sum(list(filter(lambda value: value is not None, total_completed_block_types.values()))) \
        if total_completed_block_types and total_completed_block_types.values() else 0

    block_info = {
        'total_blocks': total_blocks,
        'total_completed_blocks': total_completed_blocks,
        'total_block_types': total_block_types,
        'total_completed_block_types': total_completed_block_types
    }

    return block_info


def get_progress_statistics_by_block_types(request, course_key):
    block_info = get_progress_information(request, course_key)

    block_info['user_progress'] = _calculate_progress_for_block(block_info)
    total_block_types = block_info['total_block_types']
    total_completed_block_types = block_info['total_completed_block_types']
    accumulated_percentages_for_each_block = OrderedDict([('problem', 0), ('html', 0), ('video', 0), ('other', 0)])

    for block_type in accumulated_percentages_for_each_block.keys():
        accumulated_percentages_for_each_block[block_type] = _calculate_percentage(
            total_completed_block_types[block_type], total_block_types[block_type])

    return block_info, accumulated_percentages_for_each_block


def _calculate_percentage(completed_count, total_count):
    return format((completed_count / total_count) * 100, '.0f') if total_count > 0 else total_count


def _calculate_progress_for_block(block_info):
    total_blocks = block_info['total_blocks']
    total_completed_blocks = block_info['total_completed_blocks']

    return _calculate_percentage(total_completed_blocks, total_blocks)


def get_course_progress_percentage(request, course_key):
    """
    get course progress in percentage for a given course key
    :param request: (HttpRequest) request object
    :param course_key: (str) course key

    :return: (str) course progress i.e 70
    """

    return _calculate_progress_for_block(get_progress_information(request, course_key))


def get_and_save_course_progress(course_key, user_id):
    """Calculate and save a learner's progress"""
    # from .models import CourseProgressStats

    user = User.objects.get(id=user_id)
    request = RequestFactory().request()
    request.user = user

    progress_percentage = get_course_progress_percentage(request, course_key)

    # TODO uncomment this when we are able to handle user tab closing without clicking next to save progress
    # CourseProgressStats.objects.filter(enrollment__user=user, enrollment__course=course_key).update(
    #     progress=progress_percentage
    # )

    return progress_percentage


def get_rtl_class(course_language):
    """
    Figure out layout style class for course based on course name and its language
    """

    return 'rtl-content' if is_rtl_language(course_language) else ""


def is_rtl_language(language_code):
    """
    Check if given Language code is RTL or not
    """

    # These methods will be removed once we move to Site wise language application
    language_code = "en" if language_code == "" or language_code is None else language_code
    rtl_languages = 'ur he fa ar sd pa ps'
    return language_code in rtl_languages


def get_course_first_unit_lms_url(course_tree):
    children = course_tree.get('children')
    if not children:
        return course_tree.get('lms_web_url')

    return get_course_first_unit_lms_url(children[0])


def get_date_diff_in_days(future_date):
    """
    get date diff in days with ref to current date
    :param future_date: (datetime) a date in future

    :returns: (int) difference in days i.e 23
    """

    current_date = datetime.now().date()
    return (future_date.date() - current_date).days


def is_course_enroll_able(course):
    """
    return a boolean whether the course is enroll-able or not
    :param course: course object
    :return: boolean flag if a course can be enroll-able or not
    """
    is_course_ended = course_metadata_utils.has_course_ended(course.end)
    is_enrollment_ended = course_metadata_utils.has_course_ended(course.enrollment_end)
    is_enrollment_not_started = datetime.now(utc) < course.enrollment_start if course.enrollment_start else False
    return not is_course_ended and not is_enrollment_ended and not is_enrollment_not_started


def get_phone_validators():
    return [
        MinLengthValidator(limit_value=11, message=_('Phone number should be of minimum 11 chars.')),
        RegexValidator(message=_('Phone number can only contain numbers.'), regex='^\\+?1?\\d*$')
    ]


def validate_text_for_emoji(text):
    """
    get date diff validates the text and raise validation
    error if a emoji exists in the text
    :param text: (str) text to be validated

    :raise: validation error
    """
    pattern = re_compile(
        "(["
        "\U0001F1E0-\U0001F1FF"  # flags (iOS)
        "\U0001F300-\U0001F5FF"  # symbols & pictographs
        "\U0001F600-\U0001F64F"  # emoticons
        "\U0001F680-\U0001F6FF"  # transport & map symbols
        "\U0001F700-\U0001F77F"  # alchemical symbols
        "\U0001F780-\U0001F7FF"  # Geometric Shapes Extended
        "\U0001F800-\U0001F8FF"  # Supplemental Arrows-C
        "\U0001F900-\U0001F9FF"  # Supplemental Symbols and Pictographs
        "\U0001FA00-\U0001FA6F"  # Chess Symbols
        "\U0001FA70-\U0001FAFF"  # Symbols and Pictographs Extended-A
        "\U00002702-\U000027B0"  # Dingbats
        "])"
    )
    if text and findall(pattern, text):
        raise ValidationError(_('Invalid data! text should not contain any emoji.'))


def create_accordion_for_sidebar(request, course, course_experience_mode):
    """Create and render accordion to display in sidebar for discussion course and other templates."""

    from lms.djangoapps.courseware.views.index import render_accordion

    accordion = None
    hide_course_navigation = settings.FEATURES.get('HIDE_COURSEWARE_NAVIGATION')
    field_data_cache = FieldDataCache.cache_for_descriptor_descendents(
        course.id,
        request.user,
        course,
        depth=2,
        read_only=CrawlersConfig.is_crawler(request),
    )
    table_of_contents = toc_for_course(request.user, request, course, None, None, field_data_cache)
    if not hide_course_navigation:
        if not request.user.is_authenticated:
            course_block_tree = table_of_contents
        else:
            course_block_tree = get_course_outline_block_tree(
                request, text_type(course.id), request.user, allow_start_dates_in_future=True
            )
        accordion = render_accordion(request, course, course_block_tree, None, None, course_experience_mode)

    return accordion


def create_params_for_locked_till_payment_page(course_language, user_id, course_key):
    """Create params to show in verified track unit view for audit users."""

    ecomm_service = EcommerceService()
    modes = CourseMode.modes_for_course_dict(course_key)
    user = User.objects.get(id=user_id)
    ecommerce_checkout = ecomm_service.is_enabled(user)
    ecommerce_checkout_link = '#'
    if ecommerce_checkout:
        single_paid_mode = modes.get(CourseMode.VERIFIED, None)
        if single_paid_mode and single_paid_mode.sku:
            ecommerce_checkout_link = ecomm_service.get_checkout_page_url(single_paid_mode.sku)
    params = {
        'rtl_class': get_rtl_class(course_language),
        'ecommerce_checkout_link': ecommerce_checkout_link,
    }
    return params


def create_discount_data(registration_price, discount_percent, discount_date):
    """Calculate discounted price for courses."""
    remaining_days = (discount_date - date.today()).days if discount_date else 0
    original_price = registration_price
    if remaining_days > 0 and discount_percent and registration_price:
        original_price = int((registration_price * 100) / (100 - discount_percent))
    registration_price = format_course_price(original_price, for_about_page=True)
    return registration_price, remaining_days


def get_key_from_value(dictionary, target_value):
    for key, value in dictionary.items():
        if value == target_value:
            return key
    return None
