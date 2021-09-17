""" Overrides app util functions """

from datetime import datetime
from logging import getLogger

from completion.models import BlockCompletion
from django.conf import settings
from django.db.models import Case, IntegerField, Sum, When
from django.db.models.functions import Coalesce
from opaque_keys.edx.keys import CourseKey
from six import text_type

from lms.djangoapps.course_api.blocks.serializers import BlockDictSerializer
from lms.djangoapps.course_api.blocks.transformers.blocks_api import BlocksAPITransformer
from lms.djangoapps.courseware.courses import (
    get_courses,
    sort_by_announcement,
    sort_by_start_date
)
from openedx.core.djangoapps.content.block_structure.api import get_course_in_cache
from openedx.core.djangoapps.content.block_structure.transformers import BlockStructureTransformers
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.lib.request_utils import get_request_or_stub
from openedx.features.course_experience.utils import get_course_outline_block_tree, get_resume_block
from student.models import CourseEnrollment

log = getLogger(__name__)

CORE_BLOCK_TYPES = ['html', 'video', 'problem']
BLOCK_TYPES_TO_FILTER = [
    'course', 'chapter', 'sequential', 'vertical', 'discussion', 'openassessment', 'pb-mcq', 'pb-answer', 'pb-choice',
    'pb-message'
]


def get_featured_course():
    """
    Get featured course, if feature_course_key is set in Site Configurations

    :returns (CourseOverview): course or None
    """

    feature_course_key = configuration_helpers.get_value('feature_course_key', None)
    if feature_course_key:
        return CourseOverview.get_from_id(CourseKey.from_string(feature_course_key))


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

    content_class = ''
    course_experience_mode = "Normal"
    if hasattr(course_overview, 'custom_settings'):
        custom_settings = course_overview.custom_settings
        course_experience_mode = custom_settings.get_course_experience_display()
        content_class = 'video-course-content' if course_experience_mode == 'Video' else ''

    return course_experience_mode, content_class


def _get_resume_course_info(request, course_id, are_future_start_dates_allowed=False):
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
            has_visited_course, resume_course_url, resume_course_title = _get_resume_course_info(
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

    Arguments:
        total_block_type_counts (dict): Total block type counts of required course

    Returns:
        accumulated_data (dict): Accumulated block type counts
    """

    accumulated_data = {
        'problem': 0,
        'video': 0,
        'html': 0,
        'other': 0
    }
    if total_block_type_counts:
        for block_type, count in total_block_type_counts.items():
            if block_type in CORE_BLOCK_TYPES:
                accumulated_data[block_type] = count
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


def get_course_progress_percentage(request, course_key):
    """
    get course progress in percentage for a given course key
    :param request: (HttpRequest) request object
    :param course_key: (str) course key

    :return: (str) course progress i.e 70
    """

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
            Sum(Case(When(block_type='video', then=1), default=0, output_field=IntegerField())),
            0
        ),
        problem=Coalesce(
            Sum(Case(When(block_type='problem', then=1), default=0, output_field=IntegerField())),
            0
        ),
        html=Coalesce(
            Sum(Case(When(block_type='html', then=1), default=0, output_field=IntegerField())),
            0
        ),
        other=Coalesce(
            Sum(Case(When(block_type__in=CORE_BLOCK_TYPES, then=0), default=1, output_field=IntegerField())),
            0
        ),
    )
    total_completed_blocks = sum(list(filter(lambda value: value is not None, total_completed_block_types.values()))) \
        if total_completed_block_types and total_completed_block_types.values() else 0

    return format((total_completed_blocks / total_blocks) * 100, '.0f') if total_blocks > 0 else total_blocks


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


def get_date_diff_in_days(future_date):
    """
    get date diff in days with ref to current date
    :param future_date: (datetime) a date in future

    :returns: (int) difference in days i.e 23
    """

    current_date = datetime.now().date()
    return (future_date.date() - current_date).days
