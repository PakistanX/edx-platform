"""
Views for Admin Panel API
"""
from csv import DictReader, DictWriter
from datetime import timedelta
from io import StringIO
from itertools import groupby

from dateutil.parser import parse
from django.conf import settings
from django.contrib.auth.models import Group
from django.db.models import ExpressionWrapper, F, IntegerField, Prefetch, Q, Sum
from django.http import Http404, HttpResponse
from django.middleware import csrf
from django.utils.decorators import method_decorator
from rest_framework import generics, status, views, viewsets
from rest_framework.authentication import SessionAuthentication
from rest_framework.filters import OrderingFilter
from rest_framework.response import Response

from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.cors_csrf.decorators import ensure_csrf_cookie_cross_domain
from openedx.core.djangoapps.user_api.accounts.image_helpers import get_profile_image_urls_for_user
from openedx.features.pakx.lms.overrides.models import CourseProgressStats
from student.models import CourseAccessRole, CourseEnrollment, LanguageProficiency

from .constants import (
    BULK_REGISTRATION_TASK_SUCCESS_MSG,
    ENROLLMENT_COURSE_DIFF_ORG_ERROR_MSG,
    ENROLLMENT_COURSE_EXPIRED_MSG,
    ENROLLMENT_SUCCESS_MESSAGE,
    GROUP_ORGANIZATION_ADMIN,
    GROUP_TRAINING_MANAGERS,
    ORG_ADMIN,
    SELF_ACTIVE_STATUS_CHANGE_ERROR_MSG,
    TRAINING_MANAGER
)
from .pagination import CourseEnrollmentPagination, PakxAdminAppPagination
from .permissions import CanAccessPakXAdminPanel, IsSameOrganization
from .serializers import (
    CoursesSerializer,
    CourseStatsListSerializer,
    LearnersSerializer,
    UserCourseEnrollmentSerializer,
    UserDetailViewSerializer,
    UserListingSerializer,
    UserSerializer
)
from .tasks import bulk_user_registration, enroll_users
from .utils import (
    create_user,
    do_user_and_courses_have_same_org,
    extract_filters_and_search,
    get_completed_course_count_filters,
    get_course_overview_same_org_filter,
    get_enroll_able_course_qs,
    get_org_users_qs,
    get_request_user_org_id,
    get_roles_q_filters,
    get_user_data_from_bulk_registration_file,
    get_user_org,
    is_courses_enroll_able
)


class UserCourseEnrollmentsListAPI(generics.ListAPIView):
    """
    List API of user course enrollment
    <lms>/adminpanel/user-course-enrollments/<user_id>/

    :returns:
        {
            "count": 3,
            "next": null,
            "previous": null,
            "results": [
                {
                    "display_name": "Rohan's Practice Course",
                    "enrollment_status": "honor",
                    "enrollment_date": "2021-06-10",
                    "progress": 33.0,
                    "completion_date": null,
                    "grades": ""
                },
                {
                    "display_name": "کام کی جگہ کے آداب",
                    "enrollment_status": "honor",
                    "enrollment_date": "2021-06-09",
                    "progress": 33.0,
                    "completion_date": null,
                    "grades": ""
                }
            ],
            "total_pages": 1
        }

    """
    serializer_class = UserCourseEnrollmentSerializer
    authentication_classes = [SessionAuthentication]
    permission_classes = [CanAccessPakXAdminPanel, IsSameOrganization]
    pagination_class = CourseEnrollmentPagination
    model = CourseEnrollment

    def get_queryset(self):
        qs = CourseEnrollment.objects.filter(user_id=self.kwargs['user_id'], is_active=True)

        if not self.request.user.is_superuser:
            qs = qs.filter(course__org__iregex=get_user_org(self.request.user))

        return qs.select_related(
            'enrollment_stats',
            'course'
        ).order_by(
            '-id'
        )

    def get_serializer_context(self):
        context = super(UserCourseEnrollmentsListAPI, self).get_serializer_context()
        context.update({'request': self.request})
        return context


class UserProfileViewSet(viewsets.ModelViewSet):
    """
    User view-set for user listing/create/update/active/de-active
    """
    authentication_classes = [SessionAuthentication]
    permission_classes = [CanAccessPakXAdminPanel]
    pagination_class = PakxAdminAppPagination
    serializer_class = UserListingSerializer
    filter_backends = [OrderingFilter]
    OrderingFilter.ordering_fields = ('id', 'name', 'email', 'employee_id')
    ordering = ['-id']

    def get_object(self):
        group_qs = Group.objects.filter(name__in=[GROUP_TRAINING_MANAGERS, GROUP_ORGANIZATION_ADMIN]).order_by('name')
        completed_count, in_progress_count = get_completed_course_count_filters(req_user=self.request.user)

        user_obj = get_org_users_qs(
            self.request.user
        ).filter(
            id=self.kwargs['pk']
        ).prefetch_related(
            'courseenrollment_set'
        ).prefetch_related(
            Prefetch('groups', to_attr='staff_groups', queryset=group_qs),
        ).annotate(
            completed=completed_count,
            in_prog=in_progress_count
        ).first()

        if user_obj:
            return user_obj
        raise Http404

    def get_serializer_class(self):
        if self.action in ['retrieve', 'partial_update']:
            return UserDetailViewSerializer

        return UserListingSerializer

    def create(self, request, *args, **kwargs):
        if request.data.get('profile'):
            request.data['profile']['organization'] = get_request_user_org_id(self.request)

        is_created, res_data = create_user(request.data, request.scheme)
        if is_created:
            return Response(UserSerializer(res_data).data, status=status.HTTP_201_CREATED)

        return Response(res_data, status=status.HTTP_400_BAD_REQUEST)

    def bulk_registration(self, request, *args, **kwargs):
        if not request.FILES.get('file'):
            return Response('File is required!', status=status.HTTP_400_BAD_REQUEST)

        file_data = StringIO(request.FILES['file'].read().decode('utf-8'))
        file_reader = DictReader(file_data)

        required_col_names = {'name', 'username', 'email', 'organization_id', 'role', 'employee_id', 'language'}
        if not set(file_reader.fieldnames) == required_col_names:
            return Response(
                'Invalid column names! Correct names are: "{}"'.format('" | "'.join(required_col_names)),
                status=status.HTTP_400_BAD_REQUEST
            )

        users = get_user_data_from_bulk_registration_file(file_reader, get_request_user_org_id(self.request))
        bulk_user_registration.delay(users, request.user.email, request.scheme)

        return Response(BULK_REGISTRATION_TASK_SUCCESS_MSG, status=status.HTTP_200_OK)

    def partial_update(self, request, *args, **kwargs):
        user = self.get_object()
        request.data['profile'].update({'organization': get_request_user_org_id(self.request)})
        user_serializer = UserSerializer(user, data=request.data, partial=True)

        if user_serializer.is_valid():
            user_serializer.save()
            return Response(self.get_serializer(user).data, status=status.HTTP_200_OK)

        return Response(user_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def destroy(self, request, *args, **kwargs):
        if request.user.id == kwargs['pk']:
            return Response(SELF_ACTIVE_STATUS_CHANGE_ERROR_MSG, status=status.HTTP_403_FORBIDDEN)

        if self.get_queryset().filter(id=kwargs['pk']).update(is_active=False):
            return Response(status=status.HTTP_200_OK)

        return Response({"ID not found": kwargs['pk']}, status=status.HTTP_404_NOT_FOUND)

    def list(self, request, *args, **kwargs):
        self.queryset = self.get_queryset()
        total_users_count = self.get_queryset().count()

        roles = self.request.query_params['roles'].split(',') if self.request.query_params.get('roles') else []
        roles_qs = get_roles_q_filters(roles)
        if roles_qs:
            self.queryset = self.queryset.filter(roles_qs)

        username = self.request.query_params['username'] if self.request.query_params.get('username') else None
        if username:
            self.queryset = self.queryset.filter(username=username)

        languages = self.request.query_params['languages'].split(',') if self.request.query_params.get(
            'languages') else []

        if languages:
            self.queryset = self.queryset.filter(profile__language_proficiencies__code__in=languages)

        search = self.request.query_params.get('search', '').strip().lower()
        for s_text in search.split():
            self.queryset = self.queryset.filter(Q(profile__name__icontains=s_text) | Q(email__icontains=s_text))

        page = self.paginate_queryset(self.queryset)
        response_data = {'total_users_count': total_users_count}

        if page is not None:
            response_data['users'] = self.get_serializer(page, many=True).data
            return self.get_paginated_response(response_data)

        response_data['users'] = self.get_serializer(self.queryset, many=True).data
        return Response(response_data)

    def get_queryset(self):
        if self.request.query_params.get("ordering"):
            self.ordering = self.request.query_params['ordering'].split(',') + self.ordering

        queryset = get_org_users_qs(self.request.user).exclude(id=self.request.user.id)
        group_qs = Group.objects.filter(name__in=[GROUP_TRAINING_MANAGERS, GROUP_ORGANIZATION_ADMIN]).order_by('name')
        return queryset.select_related(
            'profile'
        ).prefetch_related(
            Prefetch('groups', to_attr='staff_groups', queryset=group_qs),
        ).annotate(
            employee_id=F('profile__employee_id'), name=F('first_name')
        ).order_by(
            *self.ordering
        ).distinct()

    def activate_users(self, request, *args, **kwargs):
        return self.change_activation_status(True, request.data["ids"])

    def deactivate_users(self, request, *args, **kwargs):
        return self.change_activation_status(False, request.data["ids"])

    def change_activation_status(self, activation_status, ids):
        """
        method to change user activation status for given user IDs
        :param activation_status: new boolean active status
        :param ids: user IDs to be updated
        :return: response with respective status
        """
        if [str(self.request.user.id)] == ids:
            return Response(SELF_ACTIVE_STATUS_CHANGE_ERROR_MSG, status=status.HTTP_403_FORBIDDEN)

        if ids == "all":
            self.get_queryset().all().update(is_active=activation_status)
            return Response(status=status.HTTP_200_OK)

        if self.get_queryset().filter(id__in=ids).update(is_active=activation_status):
            return Response(status=status.HTTP_200_OK)

        return Response(status=status.HTTP_404_NOT_FOUND)


class CourseEnrolmentViewSet(viewsets.ModelViewSet):
    """
    Course view-set for bulk enrolment task
    """
    authentication_classes = [SessionAuthentication]
    permission_classes = [CanAccessPakXAdminPanel]

    def enroll_users(self, request, *args, **kwargs):
        if not request.data.get("user_ids") or not request.data.get("course_keys"):
            return Response(
                "Invalid request data! User IDs and course keys are required",
                status=status.HTTP_400_BAD_REQUEST
            )

        if not is_courses_enroll_able(request.data["course_keys"]):
            return Response(ENROLLMENT_COURSE_EXPIRED_MSG, status=status.HTTP_400_BAD_REQUEST)

        if not do_user_and_courses_have_same_org(request.data["course_keys"], request.user):
            return Response(ENROLLMENT_COURSE_DIFF_ORG_ERROR_MSG, status=status.HTTP_400_BAD_REQUEST)

        user_qs = get_org_users_qs(request.user).filter(id__in=request.data["user_ids"]).values_list('id', flat=True)

        if len(request.data["user_ids"]) != len(user_qs):
            other_org_users = list(set(request.data["user_ids"]) - set(list(user_qs)))
            err_msg = "You don't have the permission for {} requested users".format(len(other_org_users))
            return Response(data={'users': other_org_users, 'message': err_msg}, status=status.HTTP_409_CONFLICT)

        enroll_users.delay(self.request.user.id, request.data["user_ids"], request.data["course_keys"])
        return Response(ENROLLMENT_SUCCESS_MESSAGE, status=status.HTTP_200_OK)


class AnalyticsStats(views.APIView):
    """
    API view for organization level analytics stats
    <lms>/adminpanel/analytics/stats/

    :return:
        {
            "completed_course_count": 1,
            "course_assignment_count": 7,
            "course_in_progress": 6,
            "learner_count": 4
        }
    """
    authentication_classes = [SessionAuthentication]
    permission_classes = [CanAccessPakXAdminPanel]

    def get(self, *args, **kwargs):  # pylint: disable=unused-argument
        """
        get analytics quick stats about learner and their assigned courses
        """
        user_qs = get_org_users_qs(self.request.user)
        user_ids = user_qs.values_list('id', flat=True)

        completed_count, in_progress_count = get_completed_course_count_filters(
            exclude_staff_superuser=True, req_user=self.request.user
        )
        course_stats = user_qs.annotate(
            passed=ExpressionWrapper(completed_count, output_field=IntegerField()),
            in_progress=ExpressionWrapper(in_progress_count, output_field=IntegerField())
        ).aggregate(
            completions=Sum(F('passed')), pending=Sum(F('in_progress'))
        )

        data = {
            'learner_count': len(user_ids),
            'course_in_progress': course_stats.get('pending') or 0,
            'completed_course_count': course_stats.get('completions') or 0
        }

        data['course_assignment_count'] = data['course_in_progress'] + data['completed_course_count']
        return Response(status=status.HTTP_200_OK, data=data)


class CourseStatsListAPI(generics.ListAPIView):
    """
    API view for learners list
    <lms>/adminpanel/courses/stats/
    :returns
    {
        "count": 4,
        "next": null,
        "previous": null,
        "results": [
            {
                "display_name": "Preventing Workplace Harassment",
                "enrolled": 2,
                "completed": 1,
                "in_progress": 1,
                "completion_rate": 50
            },
            {
                "display_name": "Demonstration Course",
                "enrolled": 2,
                "completed": 0,
                "in_progress": 2,
                "completion_rate": 0
            },
            {
                "display_name": "E2E Test Course",
                "enrolled": 0,
                "completed": 0,
                "in_progress": 0,
                "completion_rate": 0
            }
        ]
    }
    """

    authentication_classes = [SessionAuthentication]
    permission_classes = [CanAccessPakXAdminPanel]
    pagination_class = PakxAdminAppPagination
    serializer_class = CourseStatsListSerializer

    def get_queryset(self):
        search_text, progress_filters = extract_filters_and_search(self.request)

        completed_count, in_progress_count = get_completed_course_count_filters(
            exclude_staff_superuser=True, req_user=self.request.user
        )
        overview_qs = CourseOverview.objects.filter(
            display_name__icontains=search_text
        ) if search_text else CourseOverview.objects.all()

        if not self.request.user.is_superuser:
            overview_qs = overview_qs.filter(get_course_overview_same_org_filter(self.request.user))
        overview_qs = overview_qs.annotate(in_progress=in_progress_count, completed=completed_count)

        if progress_filters['completed'] and progress_filters['in_progress']:
            overview_qs = overview_qs.filter(Q(in_progress__gt=0) | Q(completed__gt=0))
        elif progress_filters['completed']:
            overview_qs = overview_qs.filter(in_progress=0, completed__gt=0)
        elif progress_filters['in_progress']:
            overview_qs = overview_qs.filter(in_progress__gt=0)

        return overview_qs.order_by('display_name')


class LearnerListAPI(generics.ListAPIView):
    """
    API view for learners list
    <lms>/adminpanel/analytics/learners/

    :returns:
    {
        "count": 4,
        "next": null,
        "previous": null,
        "results": [
            {
                "id": 5,
                "name": "",
                "email": "honor@example.com",
                "last_login": "2021-06-22T05:39:30.818097Z",
                "assigned_courses": 2,
                "incomplete_courses": 1,
                "completed_courses": 1
            },
            {
                "id": 7,
                "name": "",
                "email": "verified@example.com",
                "last_login": null,
                "assigned_courses": 1,
                "incomplete_courses": 1,
                "completed_courses": 0
            }
        ]
    }
    """

    authentication_classes = [SessionAuthentication]
    permission_classes = [CanAccessPakXAdminPanel]
    pagination_class = PakxAdminAppPagination
    serializer_class = LearnersSerializer

    @staticmethod
    def get_incomplete_filters():
        """Get incomplete filter"""
        return Q(enrollment_stats__email_reminder_status__lt=CourseProgressStats.COURSE_COMPLETED)

    def get_completed_filters(self, users, users_with_incomplete_courses, enrollments):
        """Get learners with 100% completions in all assigned courses"""
        users_with_all_complete_courses = users.exclude(id__in=users_with_incomplete_courses)
        return users_with_all_complete_courses, enrollments.filter(Q(
            Q(enrollment_stats__email_reminder_status=CourseProgressStats.COURSE_COMPLETED) &
            ~Q(user_id__in=users_with_incomplete_courses)
        ))

    def apply_learner_progress_filters(self, progress_filters, users, enrollments):
        """Apply filters for learner's progress."""

        if progress_filters['in_progress'] or progress_filters['completed']:
            users = users.filter(id__in=enrollments.values_list('user', flat=True).distinct())
            if not progress_filters['in_progress'] or not progress_filters['completed']:
                users_with_incomplete_courses = enrollments.filter(self.get_incomplete_filters()).values_list(
                    'user', flat=True
                ).distinct()
                if progress_filters['in_progress']:
                    users = users.filter(id__in=users_with_incomplete_courses)
                elif progress_filters['completed']:
                    users, enrollments = self.get_completed_filters(users, users_with_incomplete_courses, enrollments)

        return users, enrollments

    def get_queryset(self):
        search_text, progress_filters = extract_filters_and_search(self.request)

        user_qs = get_org_users_qs(self.request.user)
        enrollment_qs = CourseEnrollment.objects.filter(is_active=True)

        if search_text:
            user_qs = user_qs.filter(profile__name__icontains=search_text)

        if not self.request.user.is_superuser:
            enrollment_qs = enrollment_qs.filter(course__org__iregex=get_user_org(self.request.user))

        user_qs, enrollment_qs = self.apply_learner_progress_filters(progress_filters, user_qs, enrollment_qs)

        enrollments = enrollment_qs.select_related('enrollment_stats')
        return user_qs.order_by('profile__name').prefetch_related(
            Prefetch('courseenrollment_set', to_attr='enrollment', queryset=enrollments)
        )


class DownloadCSVView(LearnerListAPI, CourseStatsListAPI):
    """API for downloading CSV files according to role."""

    def __init__(self):
        """Create a dict for handling multiple modes."""

        super().__init__()
        self.modes = {
            'learner': {
                'func': self.write_learner_data,
                'filename': 'Learner Stats.csv',
                'field_names': [
                    'Name', 'Email', 'Last Login', 'Assigned Courses', 'Incomplete Courses', 'Completed Courses'
                ]
            },
            'course': {
                'func': self.write_course_data,
                'filename': 'Course Stats.csv',
                'field_names': [
                    'Course Title', 'Assigned', 'In Progress', 'Completed', 'Completion Rate'
                ]
            },
        }

    @staticmethod
    def convert_to_localtime(date_string, offset):
        if not date_string:
            return '--'
        utc_time = parse(date_string)
        local_timezone = utc_time + timedelta(hours=float(offset))
        return " {}".format(local_timezone.strftime('%I:%M %P, %d %b %Y'))

    def prepare_learner_data(self):
        """Get learner data."""

        queryset = LearnerListAPI.get_queryset(self)
        serializer = LearnersSerializer(queryset, many=True)
        return serializer.data

    def write_learner_data(self, csv_writer):
        """Write learners data to CSV."""

        learner_data = self.prepare_learner_data()
        csv_writer.writeheader()
        for row in learner_data:
            csv_writer.writerow({
                'Name': row['name'],
                'Email': row['email'],
                'Last Login': self.convert_to_localtime(row['last_login'], self.request.GET.get('offset', '0')),
                'Assigned Courses': row['assigned_courses'],
                'Incomplete Courses': row['incomplete_courses'],
                'Completed Courses': row['completed_courses']
            })

    def prepare_course_data(self):
        """Get course data."""

        queryset = CourseStatsListAPI.get_queryset(self)
        serializer = CourseStatsListSerializer(queryset, many=True)
        return serializer.data

    def write_course_data(self, csv_writer):
        """Write course data to CSV."""

        course_data = self.prepare_course_data()
        csv_writer.writeheader()
        for row in course_data:
            csv_writer.writerow({
                'Course Title': row['display_name'],
                'Assigned': row['enrolled'],
                'In Progress': row['in_progress'],
                'Completed': row['completed'],
                'Completion Rate': '{} %'.format(row['completion_rate']),
            })

    def get(self, request, *args, **kwargs):
        """Get function for download API. Returns a CSV File with applied filters."""

        mode = self.modes[request.GET.get('mode', 'learner')]
        response = HttpResponse(content_type='text/csv; charset=UTF-8-sig')
        response['Content-Disposition'] = 'attachment; filename="{}"'.format(mode['filename'])
        csv_writer = DictWriter(response, fieldnames=mode['field_names'])
        mode['func'](csv_writer)
        return response


class UserInfo(views.APIView):
    """
    API for basic user information
    """
    authentication_classes = [SessionAuthentication]
    permission_classes = [CanAccessPakXAdminPanel]

    @method_decorator(ensure_csrf_cookie_cross_domain)
    def get(self, *args, **kwargs):  # pylint: disable=unused-argument
        """
        get user's basic info
        """
        if self.request.user.is_superuser:
            languages_qs = LanguageProficiency.objects.all()
        else:
            languages_qs = LanguageProficiency.objects.filter(
                user_profile__organization=get_request_user_org_id(self.request)
            )
        all_languages = [{'code': lang[0], 'value': lang[1]} for lang in settings.ALL_LANGUAGES]
        languages = [{'code': lang.code, 'value': lang.get_code_display()} for lang in languages_qs]
        profile_image = get_profile_image_urls_for_user(self.request.user)['medium']

        user_info = {
            'profile_image': profile_image,
            'name': self.request.user.profile.name,
            'username': self.request.user.username,
            'is_superuser': self.request.user.is_superuser,
            'id': self.request.user.id,
            'csrf_token': csrf.get_token(self.request),
            'languages': [lang[0] for lang in groupby(languages)],
            'all_languages': all_languages,
            'role': None
        }
        user_groups = Group.objects.filter(
            user=self.request.user, name__in=[GROUP_TRAINING_MANAGERS, GROUP_ORGANIZATION_ADMIN]
        ).order_by('name').first()
        if user_groups:
            user_info['role'] = TRAINING_MANAGER if user_groups.name == GROUP_TRAINING_MANAGERS else ORG_ADMIN

        return Response(status=status.HTTP_200_OK, data=user_info)


class CourseListAPI(generics.ListAPIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [CanAccessPakXAdminPanel]
    pagination_class = PakxAdminAppPagination
    serializer_class = CoursesSerializer

    PakxAdminAppPagination.page_size = 5

    instructors = {}

    def get_serializer_context(self):
        context = super(CourseListAPI, self).get_serializer_context()
        context.update({"instructors": self.instructors})
        return context

    def get_queryset(self):
        queryset = CourseOverview.objects.filter(get_enroll_able_course_qs())
        if not self.request.user.is_superuser:
            queryset = queryset.filter(get_course_overview_same_org_filter(self.request.user))

        user_id = self.request.query_params.get('user_id', '').strip().lower()
        if user_id:
            courses_keys = CourseEnrollment.objects.filter(user_id=user_id, is_active=True)
            queryset = queryset.exclude(id__in=courses_keys.values_list('course', flat=True))

        search_text = self.request.query_params.get('name', '').strip().lower()
        if search_text:
            queryset = queryset.filter(display_name__icontains=search_text)

        course_access_role_qs = CourseAccessRole.objects.filter(
            course_id__in=queryset.values_list('id')
        ).select_related(
            'user__profile'
        )

        for course_access_role in course_access_role_qs:
            course_instructors = self.instructors.get(course_access_role.course_id, [])
            instructor_name = course_access_role.user.profile.name or course_access_role.user.username
            if instructor_name not in course_instructors:
                course_instructors.append(instructor_name)
            self.instructors[course_access_role.course_id] = course_instructors

        return queryset


class UserSearchInputListAPI(views.APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [CanAccessPakXAdminPanel]

    def get(self, *args, **kwargs):  # pylint: disable=unused-argument
        qs = get_org_users_qs(self.request.user).exclude(id=self.request.user.id)
        users = {user.id: {'email': user.email} for user in qs}
        return Response(status=status.HTTP_200_OK, data={'users': users})
