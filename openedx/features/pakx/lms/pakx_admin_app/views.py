import uuid
from django.contrib.auth.models import User
from django.db.models import F
from rest_framework import viewsets, status
from rest_framework.authentication import SessionAuthentication, BasicAuthentication
from rest_framework.filters import OrderingFilter
from rest_framework.response import Response

from student.models import UserProfile

from .permissions import CanAccessPakXAdminPanel
from .pagination import PakxAdminAppPagination
from .serializers import UserSerializer, BasicUserSerializer, UserProfileSerializer
from .helpers import get_roles_q_filters, specify_user_role, send_registration_email


class UserProfileViewSet(viewsets.ModelViewSet):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [CanAccessPakXAdminPanel]
    pagination_class = PakxAdminAppPagination
    serializer_class = UserSerializer
    filter_backends = [OrderingFilter]
    OrderingFilter.ordering_fields = ('id', 'name', 'email', 'employee_id')
    ordering = ['-id']

    def create(self, request, *args, **kwargs):
        profile_data = request.data.pop('profile', None)
        role = request.data.pop('role', None)
        request.data['password'] = uuid.uuid4().hex[:8]
        user_serializer = BasicUserSerializer(data=request.data)
        if user_serializer.is_valid():
            user = user_serializer.save()
            profile_data['user'] = user.id
            profile_serializer = UserProfileSerializer(data=profile_data)
            if profile_serializer.is_valid():
                user_profile = profile_serializer.save()
                specify_user_role(user, role)
                protocol = request.scheme
                send_registration_email(user, user_profile, protocol)
                return Response(self.get_serializer(user).data, status=status.HTTP_200_OK)

            return Response(profile_serializer.errors,
                            status=status.HTTP_400_BAD_REQUEST
                            )

        return Response(user_serializer.errors,
                        status=status.HTTP_400_BAD_REQUEST
                        )

    def partial_update(self, request, *args, **kwargs):
        user = self.get_object()
        user_profile = UserProfile.objects.get(user=user)
        specify_user_role(user, request.data.pop("role", None))
        user_profile_data = request.data.pop('profile', {})
        user_data = request.data
        user_serializer = BasicUserSerializer(user, data=user_data, partial=True)
        profile_serializer = UserProfileSerializer(user_profile, data=user_profile_data, partial=True)
        if user_serializer.is_valid() and profile_serializer.is_valid():
            user_serializer.save()
            profile_serializer.save()
            return Response(self.get_serializer(user).data, status=status.HTTP_200_OK)
        return Response({**user_serializer.errors, **profile_serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

    def destroy(self, request, *args, **kwargs):
        if self.get_queryset().filter(id=kwargs['pk']).update(is_active=False):
            return Response(status=status.HTTP_200_OK)

        return Response({"ID not found": kwargs['pk']}, status=status.HTTP_404_NOT_FOUND)

    def list(self, request, *args, **kwargs):
        self.queryset = self.get_queryset()

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
            self.queryset = self.queryset.filter(profile__language__in=languages)

        page = self.paginate_queryset(self.queryset)
        if page is not None:
            return self.get_paginated_response(self.get_serializer(page, many=True).data)

        return Response(self.get_serializer(self.queryset, many=True).data)

    def get_queryset(self):
        if self.request.query_params.get("ordering"):
            self.ordering = self.request.query_params['ordering'].split(',') + self.ordering

        if self.request.user.is_superuser:
            queryset = User.objects.all()
        else:
            queryset = User.objects.filter(
                attributes__name="org",
                attributes__value=self.request.user.attributes.filter(name="org").first().value
            )

        return queryset.annotate(employee_id=F('profile__employee_id'), name=F('first_name')).order_by(*self.ordering)

    def activate_users(self, request, *args, **kwargs):
        return self.change_activation_status(True, request.data["ids"])

    def deactivate_users(self, request, *args, **kwargs):
        return self.change_activation_status(False, request.data["ids"])

    def change_activation_status(self, activation_status, ids):
        if ids == "all":
            self.get_queryset().all().update(is_active=activation_status)
            return Response(status=status.HTTP_200_OK)

        if self.get_queryset().filter(id__in=ids).update(is_active=activation_status):
            return Response(status=status.HTTP_200_OK)

        return Response(status=status.HTTP_404_NOT_FOUND)
