from django.db.models import Case, When
from django.urls import reverse
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from six import text_type

from course_modes.models import get_course_prices
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.features.pakx.lms.discover.authentications import DiscoverAuthentication
from openedx.features.pakx.lms.overrides.utils import (
    _get_org_log,
    get_or_create_course_overview_content,
    get_organization_by_short_name,
    is_blank_str
)


class CourseDataView(APIView):
    """Get course card data."""

    # authentication_classes = [DiscoverAuthentication, ]

    @staticmethod
    def get_courses(course_ids):
        """Get course list from IDs."""
        preserved = Case(*[When(custom_settings__course=course_id, then=pos) for pos, course_id in enumerate(course_ids)])
        return CourseOverview.objects.filter(
            custom_settings__course__in=course_ids
        ).order_by(preserved)

    def create_course_card_dict(self, data, org_logo_url, org_name, course, is_upcoming):
        """Create dict from provided data."""
        raise NotImplementedError

    def get_course_card_data(self, course, is_upcoming=False):
        """
        Get course data required for home page course card

        :returns (dict): dict of course card data
        """
        if not isinstance(course, CourseOverview):
            course = CourseOverview.objects.filter(id=course.id).first()
        course_custom_setting = get_or_create_course_overview_content(course.id)

        course_org = get_organization_by_short_name(course.org)
        org_logo_url = course_custom_setting.publisher_card_logo_url or _get_org_log(course_org)
        org_name = course_org.get('name') if is_blank_str(course_custom_setting.publisher_name) else \
            course_custom_setting.publisher_name
        course_experience_type = 'VIDEO' if course_custom_setting.course_experience else 'NORMAL'
        pakx_short_logo = '/static/pakx/images/mooc/pakx-logo.png'

        data = {
            'org_name': org_name,
            'course_image_url': self.request.build_absolute_uri(course.course_image_url),
            'course_name': course.display_name_with_default,
            'org_logo_url': self.request.build_absolute_uri(org_logo_url or pakx_short_logo),
            'course_description': course_custom_setting.card_description,
            'course_type': course_experience_type,
            'about_page_url': self.request.build_absolute_uri(
                reverse('about_course', kwargs={'course_id': text_type(course.id)})
            ),
            'tag': 'Course'
        }
        return self.create_course_card_dict(data, org_logo_url, org_name, course, is_upcoming)


class CoursesListView(CourseDataView):
    """Get list of upcoming and featured courses for discovery website."""

    def create_course_card_dict(self, data, org_logo_url, org_name, course, is_upcoming):
        """
        Get course data required for home page course card

        :returns (dict): dict of course card data
        """

        data['org_name'] = 'LUMS' if org_name == 'LUMSx' else org_name
        data.pop('course_description')

        if is_upcoming:
            data.pop('about_page_url')
        else:
            _, course_price = get_course_prices(course, for_about_page=True)
            data.update({
                'course_price': course_price.replace('PKR', 'Rs.'),
            })

        return data

    def post(self, request):
        """List courses."""
        upcoming_courses_ids = request.data.get('UPCOMING_COURSES', []) or []
        featured_courses_ids = request.data.get('FEATURED_COURSES', []) or []

        upcoming_courses = self.get_courses(upcoming_courses_ids)
        featured_courses = self.get_courses(featured_courses_ids)

        return Response({
            'upcoming_courses': [self.get_course_card_data(course, True) for course in upcoming_courses],
            'featured_courses': [self.get_course_card_data(course) for course in featured_courses]
        }, status=status.HTTP_200_OK)


class BusinessCoursesView(CourseDataView):
    """Get list of business courses for discovery website."""

    def create_course_card_dict(self, data, org_logo_url, org_name, course, is_upcoming):
        """
        Get course data required for home page course card

        :returns (dict): dict of course card data
        """
        default_logo = '/static/pakx/images/mooc/pakx-logo.png'
        if text_type(course.id) == 'course-v1:LUMSx+2+2022':
            default_logo = '/static/pakx/images/lums-k-logo.png'

        data['org_logo_url'] = self.request.build_absolute_uri(org_logo_url or default_logo)
        data.pop('course_type')
        return data

    def post(self, request):
        """List courses."""
        business_courses_ids = request.data.get('BUSINESS_COURSES', []) or []
        business_courses = self.get_courses(business_courses_ids)

        return Response({
            'courses': [self.get_course_card_data(course) for course in business_courses],
        }, status=status.HTTP_200_OK)
