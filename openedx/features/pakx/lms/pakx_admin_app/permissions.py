"""
Permissions for PakX Admin Panel APIs.
"""
from django.contrib.auth.models import User
from rest_framework.permissions import BasePermission

from .constants import GROUP_ORGANIZATION_ADMIN, GROUP_TRAINING_MANAGERS
from .utils import get_user_same_org_filter


class CanAccessPakXAdminPanel(BasePermission):
    """
    permission to access the PakX admin panel
    """
    message = 'User does not have the permission for Admin Panel.'

    def has_permission(self, request, view):
        return request.user.is_superuser or User.objects.filter(
            groups__name__in=[GROUP_TRAINING_MANAGERS, GROUP_ORGANIZATION_ADMIN],
            profile__organization__isnull=False,
            id=request.user.id
        ).exists()


class IsSameOrganization(BasePermission):
    """
    permission to access a particular user's data
    """
    message = 'Users does not have the same organization.'

    def has_permission(self, request, view):
        return request.user.is_superuser or User.objects.filter(
            id=view.kwargs.get('user_id')
        ).filter(
            get_user_same_org_filter(request.user),
        ).exists()


# Dialog Academy Enrollments Specific
class DialogAcademyIsStaffOrAllowedEmail(BasePermission):
    """
    Allows access to:
    1. Django Staff users (is_staff=True)
    2. Authenticated users with a specific allowed email address (or domain).
    """

    ALLOWED_EMAILS = ['anum@thebtceducation.com']
    ALLOWED_DOMAINS = ['thebtceducation.com']

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        user_domain = request.user.email.split('@')[-1]
        if request.user.is_staff or \
            request.user.email in self.ALLOWED_EMAILS or \
            user_domain in self.ALLOWED_DOMAINS:
            return True


        return False
