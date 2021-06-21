"""
Serializer for Admin Panel APIs
"""
from django.contrib.auth.models import User
from rest_framework import serializers

from student.models import UserProfile

from .constants import GROUP_TRAINING_MANAGERS, LEARNER, ORG_ADMIN, TRAINING_MANAGER


class UserSerializer(serializers.ModelSerializer):
    """
    Serializer User's view-set list view
    """
    employee_id = serializers.CharField(source='profile.employee_id')
    language = serializers.CharField(source='profile.language')
    name = serializers.CharField(source='get_full_name')
    organization = serializers.CharField(source='profile.organization')
    role = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ('username', 'id', 'email', 'name', 'employee_id', 'organization', 'language', 'is_active', 'role', 'date_joined', 'last_login')

    def get_role(self, obj):
        if obj.staff_groups:
            return TRAINING_MANAGER if obj.staff_groups[0].name == GROUP_TRAINING_MANAGERS else ORG_ADMIN

        return LEARNER


class BasicUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('username', 'first_name', 'last_name', 'email')


class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserProfile
        fields = ('user', 'employee_id', 'language', 'organization')
