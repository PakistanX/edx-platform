"""
Commerce views
"""


import logging

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.http import Http404
from edx_ace import Recipient, ace
from edx_rest_api_client import exceptions
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from opaque_keys.edx.keys import CourseKey
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import NotAuthenticated
from rest_framework.generics import ListAPIView, RetrieveUpdateAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from course_modes.models import CourseMode
from openedx.core.djangoapps.ace_common.template_context import get_base_template_context
from openedx.core.djangoapps.commerce.utils import ecommerce_api_client
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.lib.api.authentication import BearerAuthentication
from openedx.core.lib.api.mixins import PutAsCreateMixin
from openedx.core.lib.celery.task_utils import emulate_http_request
from openedx.features.pakx.lms.pakx_admin_app.message_types import CommerceEnrol, CommerceCODOrder
from student.models import CourseEnrollment
from util.json_request import JsonResponse

from ...utils import is_account_activation_requirement_disabled
from .models import Course
from .permissions import ApiKeyOrModelPermission, IsAuthenticatedOrActivationOverridden
from .serializers import CourseSerializer

log = logging.getLogger(__name__)


class CourseListView(ListAPIView):
    """ List courses and modes. """
    authentication_classes = (JwtAuthentication, BearerAuthentication, SessionAuthentication,)
    permission_classes = (IsAuthenticated,)
    serializer_class = CourseSerializer
    pagination_class = None

    def get_queryset(self):
        return list(Course.iterator())


class CourseRetrieveUpdateView(PutAsCreateMixin, RetrieveUpdateAPIView):
    """ Retrieve, update, or create courses/modes. """
    lookup_field = 'id'
    lookup_url_kwarg = 'course_id'
    model = CourseMode
    authentication_classes = (JwtAuthentication, BearerAuthentication, SessionAuthentication,)
    permission_classes = (ApiKeyOrModelPermission,)
    serializer_class = CourseSerializer

    # Django Rest Framework v3 requires that we provide a queryset.
    # Note that we're overriding `get_object()` below to return a `Course`
    # rather than a CourseMode, so this isn't really used.
    queryset = CourseMode.objects.all()

    def get_object(self, queryset=None):
        course_id = self.kwargs.get(self.lookup_url_kwarg)
        course = Course.get(course_id)

        if course:
            return course

        raise Http404

    def pre_save(self, obj):
        # There is nothing to pre-save. The default behavior changes the Course.id attribute from
        # a CourseKey to a string, which is not desired.
        pass


class OrderView(APIView):
    """ Retrieve order details. """

    authentication_classes = (JwtAuthentication, SessionAuthentication,)
    permission_classes = (IsAuthenticatedOrActivationOverridden,)

    def get(self, request, number):
        """ HTTP handler. """
        # If the account activation requirement is disabled for this installation, override the
        # anonymous user object attached to the request with the actual user object (if it exists)
        if not request.user.is_authenticated and is_account_activation_requirement_disabled():
            try:
                request.user = User.objects.get(id=request.session._session_cache['_auth_user_id'])
            except User.DoesNotExist:
                return JsonResponse(status=403)
        try:
            order = ecommerce_api_client(request.user).orders(number).get()
            return JsonResponse(order)
        except exceptions.HttpNotFoundError:
            return JsonResponse(status=404)


class EnrollmentNotification(APIView):
    """Send enrollment notification to user."""

    @staticmethod
    def _send_course_enrolment_email(username, course_key):
        """
        send a course enrolment notification via email
        :param user: (User) request User
        :param course_key: (str) course key
        """
        site = Site.objects.get_current()
        user = User.objects.filter(username=username).first()
        email_context = get_base_template_context(site, user)
        course_overview = CourseOverview.objects.get(id=CourseKey.from_string(course_key))
        email_context.update({
            'course': course_overview.display_name,
            'image_url': 'https://' + site.domain + course_overview.course_image_url,
            'url': "https://{}/courses/{}/overview".format(site.domain, course_key),
        })

        with emulate_http_request(site, user):
            email_context.update({
                'site_name': site.domain,
                'dashboard_url': 'https://' + site.domain + '/dashboard',
                'platform_name': configuration_helpers.get_value('PLATFORM_NAME', settings.PLATFORM_NAME),
            })

            message = CommerceEnrol().personalize(
                recipient=Recipient(email_context['username'], email_context['email']),
                language='en',
                user_context=email_context,
            )
            ace.send(message)

    @staticmethod
    def _authenticate_and_verify(host_name, username, course_key):
        """Perform authentication and verify data."""

        # TODO: Write authentication logic here
        # if host_name != settings.ECOMMERCE_PUBLIC_URL_ROOT:
        #     log.error('API call from unauthenticated source: {}'.format(host_name))
        #     raise NotAuthenticated
        if len(CourseEnrollment.objects.filter(user__username=username, course=course_key)):
            return True, None
        log.error('User {} not enrolled in course: {}'.format(username, course_key))
        return False, 404

    def get(self, request, username, course_id):
        """Send enrollment notification to user from ecommerce."""
        log.info('Enrollment email notification request for {} and {}'.format(username, course_id))

        is_verified, response_code = self._authenticate_and_verify(request.META.get('HTTP_HOST'), username, course_id)
        if not is_verified:
            return JsonResponse(status=response_code)

        self._send_course_enrolment_email(username, course_id)

        return JsonResponse(status=200)


class CodOrderNotification(APIView):
    """Send cod order notification to user."""

    @staticmethod
    def _send_cod_order_email(username, course_key, tracking_id):
        """
        send a cod order notification via email
        :param user: (User) request User
        :param course_key: (str) course key
        :param tracking_id: (str) tracking id
        """
        site = Site.objects.get_current()
        user = User.objects.filter(username=username).first()
        email_context = get_base_template_context(site, user)
        course_overview = CourseOverview.objects.get(id=CourseKey.from_string(course_key))
        email_context.update({
            'course': course_overview.display_name,
            'image_url': 'https://' + site.domain + course_overview.course_image_url,
            'url': "https://{}/courses/{}/overview".format(site.domain, course_key),
        })

        with emulate_http_request(site, user):
            email_context.update({
                'site_name': site.domain,
                'dashboard_url': 'https://' + site.domain + '/dashboard',
                'platform_name': configuration_helpers.get_value('PLATFORM_NAME', settings.PLATFORM_NAME),
                'tracking_id': tracking_id,
            })

            message = CommerceCODOrder().personalize(
                recipient=Recipient(email_context['username'], email_context['email']),
                language='en',
                user_context=email_context,
            )
            ace.send(message)


    def get(self, request, username, course_id, tracking_id):
        """Send COD order notification to user from ecommerce."""
        log.info('COD order email notification request for {} and {}'.format(username, course_id))

        self._send_cod_order_email(username, course_id, tracking_id)

        return JsonResponse(status=200)
