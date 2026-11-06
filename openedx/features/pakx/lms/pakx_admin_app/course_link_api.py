"""
Admin-panel API for tracked course links (alias slugs / utm sources) and their visit stats.

Reads are open to every admin-panel account but scoped to the client's organization (Django
staff see everything); creating clients and links is staff-only.
"""
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError, transaction
from django.db.models import Count
from django.urls import reverse
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from organizations.models import Organization
from rest_framework import serializers, status, viewsets
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import action
from rest_framework.response import Response

from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.features.pakx.lms.overrides.waffle import course_link_tracking_enabled

from .course_links import (
    client_stats,
    link_url,
    parse_report_range,
    link_errors,
    slug_mappings
)
from .models import CourseLink, CourseLinkClient
from .permissions import CanAccessPakXAdminPanel, IsStaffOrSuperUser
from .utils import get_selected_org, is_unrestricted_admin


def report_url(client):
    """Absolute URL of the client's public report page, or None when it is switched off."""
    if not client.is_report_enabled:
        return None
    return '{}{}'.format(
        settings.LMS_ROOT_URL.rstrip('/'), reverse('course-link-report', args=[str(client.report_token)])
    )


def visible_clients(request):
    """
    Clients the requester may see: Django staff see all (or just one organization's when they picked it
    with ?org=, like the rest of the admin panel); everyone else sees those linked to their own organization.
    """
    clients = CourseLinkClient.objects.all()
    user = request.user
    if is_unrestricted_admin(user):
        selected_org = get_selected_org(request)
        return clients.filter(organization__short_name__iexact=selected_org) if selected_org else clients
    org_id = getattr(getattr(user, 'profile', None), 'organization_id', None)
    return clients.filter(organization_id=org_id) if org_id else clients.none()


class CourseLinkClientSerializer(serializers.ModelSerializer):
    """A client that gets tracked links."""
    organization = serializers.PrimaryKeyRelatedField(
        queryset=Organization.objects.filter(active=True), allow_null=True, required=False
    )
    organization_name = serializers.CharField(source='organization.name', read_only=True, default=None)
    report_url = serializers.SerializerMethodField()
    link_count = serializers.SerializerMethodField()

    class Meta:
        model = CourseLinkClient
        fields = (
            'id', 'name', 'organization', 'organization_name', 'is_report_enabled', 'report_url',
            'link_count', 'created_at',
        )
        read_only_fields = ('created_at',)

    def get_report_url(self, obj):
        return report_url(obj)

    def get_link_count(self, obj):
        count = getattr(obj, 'link_total', None)  # annotated by the viewset's list queryset
        return obj.links.count() if count is None else count


class CourseLinkSerializer(serializers.ModelSerializer):
    """Create a tracked link: an alias slug or a utm source for a course with a custom URL."""
    course = serializers.CharField(write_only=True, help_text="Course key, e.g. course-v1:PSW+TL09+2025")
    course_id = serializers.SerializerMethodField()
    course_name = serializers.SerializerMethodField()
    client_name = serializers.CharField(source='client.name', read_only=True)
    url = serializers.SerializerMethodField()
    label = serializers.CharField(read_only=True)

    class Meta:
        model = CourseLink
        fields = (
            'id', 'client', 'client_name', 'course', 'course_id', 'course_name', 'kind', 'value', 'utm_medium',
            'utm_campaign', 'utm_term', 'utm_content', 'label', 'is_active', 'url', 'created_at',
        )
        read_only_fields = ('created_at',)

    def get_unique_together_validators(self):
        return []  # duplicates are checked in validate(); `course` is written as a key string

    def get_course_id(self, obj):
        return str(obj.course_id)

    def get_course_name(self, obj):
        try:
            return obj.course.display_name
        except ObjectDoesNotExist:  # the course was deleted; the link and its stats stay
            return ''

    def get_url(self, obj):
        # the context dict is shared by every row of a list response, so the slug maps load once
        return link_url(obj, self.context.setdefault('slug_mappings', slug_mappings()))

    def validate_course(self, value):
        try:
            course_key = CourseKey.from_string(value)
        except InvalidKeyError:
            raise serializers.ValidationError('Invalid course key.')
        course = CourseOverview.objects.filter(id=course_key).first()
        if course is None:
            raise serializers.ValidationError('Course not found.')
        return course

    def validate(self, attrs):
        attrs['value'] = attrs['value'].strip().lower()
        for field in CourseLink.UTM_EXTRA_FIELDS:
            attrs[field] = attrs.get(field, '').strip().lower()
        errors = link_errors(
            attrs['kind'], attrs['value'], attrs['course'], extras=attrs, client_id=attrs['client'].pk
        )
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class CourseLinkActiveSerializer(CourseLinkSerializer):
    """Updates only toggle is_active: changing a live alias or source would break links already shared."""

    class Meta(CourseLinkSerializer.Meta):
        read_only_fields = CourseLinkSerializer.Meta.read_only_fields + (
            ('client', 'course', 'kind', 'value') + CourseLink.UTM_EXTRA_FIELDS
        )

    course = serializers.CharField(read_only=True)

    def validate(self, attrs):
        return attrs


class _CourseLinkBaseViewSet(viewsets.ModelViewSet):
    authentication_classes = [SessionAuthentication]
    pagination_class = None
    http_method_names = ['get', 'post', 'patch', 'head', 'options']  # no delete: it would drop the stats

    def get_permissions(self):
        if self.action in ('list', 'retrieve', 'stats'):
            return [CanAccessPakXAdminPanel()]
        return [IsStaffOrSuperUser()]


class CourseLinkClientViewSet(_CourseLinkBaseViewSet):
    """
    <lms>/adminpanel/course-link-clients/            list / create (staff); staff may narrow with ?org=<short_name>
    <lms>/adminpanel/course-link-clients/<id>/       retrieve / patch (staff)
    <lms>/adminpanel/course-link-clients/<id>/stats/?days=30|from=YYYY-MM-DD&to=YYYY-MM-DD
    <lms>/adminpanel/course-link-clients/<id>/regenerate_token/   POST (staff)
    """
    serializer_class = CourseLinkClientSerializer

    def get_queryset(self):
        return visible_clients(self.request).select_related('organization').annotate(link_total=Count('links'))

    @action(detail=True, methods=['get'])
    def stats(self, request, pk=None):  # pylint: disable=unused-argument
        client = self.get_object()
        params = request.query_params
        try:
            start, end = parse_report_range(params.get('from'), params.get('to'), params.get('days'))
        except ValueError as error:
            return Response({'error': str(error)}, status=status.HTTP_400_BAD_REQUEST)
        data = client_stats(client, start, end)
        data['tracking_enabled'] = course_link_tracking_enabled()
        data['report_url'] = report_url(client)
        return Response(data)

    @action(detail=True, methods=['post'])
    def regenerate_token(self, request, pk=None):  # pylint: disable=unused-argument
        client = self.get_object()
        client.report_token = uuid4()
        client.save(update_fields=['report_token'])
        return Response({'report_url': report_url(client)})


class CourseLinkViewSet(_CourseLinkBaseViewSet):
    """
    <lms>/adminpanel/course-links/?client=<id>   list
    <lms>/adminpanel/course-links/               create (staff): {client, course, kind: alias|utm, value,
                                                 optional utm_medium, utm_campaign, utm_term, utm_content};
                                                 the course must belong to the client's organization
    <lms>/adminpanel/course-links/<id>/          patch (staff): {is_active}
    """

    def perform_create(self, serializer):
        try:
            with transaction.atomic():
                serializer.save()
        except IntegrityError:  # a concurrent request took the same alias / source first
            raise serializers.ValidationError({'value': 'This link is already in use.'})

    def get_serializer_class(self):
        return CourseLinkActiveSerializer if self.action in ('update', 'partial_update') else CourseLinkSerializer

    def get_queryset(self):
        links = CourseLink.objects.filter(client__in=visible_clients(self.request)).select_related(
            'client'
        ).prefetch_related('course')
        client_id = self.request.query_params.get('client')
        return links.filter(client_id=client_id) if client_id and client_id.isdigit() else links
