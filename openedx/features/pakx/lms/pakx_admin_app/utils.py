"""
helpers functions for Admin Panel API
"""
import json
from datetime import datetime
from uuid import uuid4

import pytz
from django.conf import settings
from django.contrib.auth.models import Group, User
from django.contrib.sites.models import Site
from django.db.models import Count, Q
from django.urls import reverse
from edx_ace import ace
from edx_ace.recipient import Recipient

from openedx.core.djangoapps.ace_common.template_context import get_base_template_context
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.features.pakx.lms.overrides.models import CourseProgressStats
from student.models import Registration

from .constants import GROUP_ORGANIZATION_ADMIN, GROUP_TRAINING_MANAGERS, LEARNER, ORG_ADMIN, TRAINING_MANAGER
from .message_types import RegistrationNotification


def get_user_org_filter(user):
    return {'profile__organization_id': user.profile.organization_id}


def get_user_org(user):
    """get org for given user object"""

    user_org = getattr(user.profile.organization, "short_name", "").lower()
    if user_org == 'arbisoft':  # TODO: REMOVE THIS IF WHEN WORK PLACE ESSENTIALS COURSES ARE REMOVED
        return r'({}|{})'.format(user_org, 'pakx')
    return r'({})'.format(user_org)


def get_course_overview_same_org_filter(user):
    """get same org filter with respect course's org"""

    return Q(org__iregex=get_user_org(user))


def get_user_same_org_filter(user):
    """get same org filter with respect to user's profile-> org"""

    return Q(profile__organization__short_name__iregex=get_user_org(user))


def get_learners_filter():
    """get learners filter, excludes dummy emails & add org condition """
    return Q(
        Q(is_superuser=False) & Q(is_staff=False) & ~(Q(email__icontains='fake') | Q(email__icontains='example'))
    )


def get_user_enrollment_same_org_filter(user):
    """get filter against enrollment record and user's course enrollment, enrollment->course->org"""
    user_org = get_user_org(user)
    return Q(
        Q(courseenrollment__user__profile__organization__short_name__iregex=user_org) &
        Q(courseenrollment__course__org__iregex=user_org)
    )


def get_roles_q_filters(roles):
    """
    return Q filter to be used for filter user queryset
    :param roles: request params to filter roles
    :param user: (User) user object

    :return: Q filters
    """
    qs = Q()

    for role in roles:
        if int(role) == ORG_ADMIN:
            qs |= Q(groups__name=GROUP_ORGANIZATION_ADMIN)
        elif int(role) == LEARNER:
            qs |= get_learners_filter()
        elif int(role) == TRAINING_MANAGER:
            qs |= Q(groups__name=GROUP_TRAINING_MANAGERS)

    return qs


def specify_user_role(user, role):
    g_admin, g_tm = Group.objects.filter(
        name__in=[GROUP_ORGANIZATION_ADMIN, GROUP_TRAINING_MANAGERS]
    ).order_by('name')

    if role == ORG_ADMIN:
        user.groups.add(g_admin)
        user.groups.remove(g_tm)
    elif role == TRAINING_MANAGER:
        user.groups.remove(g_admin)
        user.groups.add(g_tm)
    elif role == LEARNER:
        user.groups.remove(g_admin, g_tm)


def get_user_data_from_bulk_registration_file(file_reader, default_org_id):
    def clean(str_to_clean):
        return str_to_clean.strip() if isinstance(str_to_clean, str) else str_to_clean

    users = []
    for user_map in file_reader:
        user = {
            'role': clean(user_map.get('role', '')),
            'email': clean(user_map.get('email', '')),
            'username': clean(user_map.get('username', '')),
            'profile': {
                'name': clean(user_map.get('name', '').title()),
                'employee_id': clean(user_map.get('employee_id')),
                'language_code': {'code': clean(user_map.get('language', ''))},
                'organization': clean(user_map.get('organization_id')) or default_org_id,
            }
        }
        users.append(user)
    return users


def create_user(user_data, request_url_scheme, next_url=''):
    """
    util function
    :param user_data: user data for registration
    :param request_url_scheme: variable containing http or https
    :param next_url: variable containing next url in email CTA
    :return: error if validation failed else None
    """
    user_data['password'] = uuid4().hex[:8]
    from .serializers import UserSerializer
    user_serializer = UserSerializer(data=user_data)

    if not user_serializer.is_valid():
        return False, {**user_serializer.errors}

    user = user_serializer.save()
    send_registration_email(user, user_data['password'], request_url_scheme, next_url=next_url)
    return True, user


def get_registration_email_message_context(user, password, protocol, is_public_registration, next_url=''):
    """
    return context for registration notification email body
    """
    site = Site.objects.get_current()
    message_context = {
        'site_name': site.domain
    }
    message_context.update(get_base_template_context(site, user=user))
    link = reverse('signin_user')
    if next_url:
        link = '{}?next={}'.format(link, next_url)
    message_context.update({
        'is_public_registration': is_public_registration,
        'platform_name': configuration_helpers.get_value('PLATFORM_NAME', settings.PLATFORM_NAME),
        'password': password,
        'email': user.email,
        'employee_id': user.profile.employee_id,
        'language': user.profile.language,
        'account_activation_link': '{protocol}://{site}{link}'.format(
            protocol=protocol,
            site=configuration_helpers.get_value('SITE_NAME', settings.SITE_NAME),
            link=link,
        )
    })
    return message_context


def get_completed_course_count_filters(exclude_staff_superuser=True, req_user=None):
    completed = Q(
        Q(courseenrollment__enrollment_stats__email_reminder_status=CourseProgressStats.COURSE_COMPLETED) &
        Q(courseenrollment__is_active=True)
    )
    in_progress = Q(
        Q(courseenrollment__enrollment_stats__email_reminder_status__lt=CourseProgressStats.COURSE_COMPLETED) &
        Q(courseenrollment__is_active=True)
    )

    is_exclude = not exclude_staff_superuser
    learners = Q(courseenrollment__user__is_staff=is_exclude) & Q(courseenrollment__user__is_superuser=is_exclude)

    if req_user and not req_user.is_superuser:
        learners = Q(learners & get_user_enrollment_same_org_filter(req_user))

    completed_count = Count("courseenrollment", filter=Q(learners & completed))
    in_progress_count = Count("courseenrollment", filter=Q(learners & in_progress))
    return completed_count, in_progress_count


def extract_filters_and_search(request):
    return request.GET.get('search', ''), json.loads(
        request.GET.get('progress_filters', '{"in_progress": false, "completed": false}')
    )


def get_org_users_qs(user):
    """
    return users from the same organization as of the request.user
    """
    queryset = User.objects.filter(get_learners_filter())
    if not user.is_superuser:
        queryset = queryset.filter(get_user_same_org_filter(user))

    return queryset.select_related(
        'profile'
    )


def get_enroll_able_course_qs():
    """
    :return: Q filter for enroll-able courses based on course enrollment & course_end dates
    """
    now = datetime.now(pytz.UTC)
    # A Course is "enroll-able" if its enrollment start date has passed,
    # is now, or is None, and its enrollment end date is in the future or is None.
    return (
        (Q(enrollment_start__lte=now) | Q(enrollment_start__isnull=True)) &
        (Q(enrollment_end__gt=now) | Q(enrollment_end__isnull=True)) &
        (Q(end_date__gt=now) | Q(end_date__isnull=True))
    )


def get_user_available_course_qs(user):
    """
    :return: Q filter for enroll-able courses for a user based on its org and course enrollment & course_end dates
    """
    return get_enroll_able_course_qs() & get_course_overview_same_org_filter(user)


def is_courses_enroll_able(course_keys):
    """
    Check if given courses can be enroll-able
    :param course_keys: list of course keys
    :return: (bool) boolean flag representing course enroll-able status
    """
    courses_qs = CourseOverview.objects.filter(id__in=course_keys)
    return courses_qs.filter(get_enroll_able_course_qs()).count() == len(course_keys)


def do_user_and_courses_have_same_org(course_keys, user, exempt_super_user=True):
    """
    Check if all courses have same org as the user
    :param course_keys: list of course keys
    :param user: user object
    :param exempt_super_user: bool flag to exempt super users from this check
    :return: (bool) boolean flag representing all courses have same org as the user
    """
    if exempt_super_user and user.is_superuser:
        return True

    courses_qs = CourseOverview.objects.filter(id__in=course_keys)
    user_org_courses_count = courses_qs.filter(get_course_overview_same_org_filter(user)).count()
    return user_org_courses_count == len(course_keys)


def get_request_user_org_id(request):
    """
    return organization ID of request user
    :param request: request obj
    :return: (int) user's organization ID
    """
    return request.user.profile.organization_id


def send_registration_email(user, password, protocol, is_public_registration=False, next_url=''):
    """
    send a registration notification via email
    """
    message = RegistrationNotification().personalize(
        recipient=Recipient(user.username, user.email),
        language=user.profile.language,
        user_context=get_registration_email_message_context(
            user, password, protocol, is_public_registration, next_url=next_url
        ),
    )
    ace.send(message)
