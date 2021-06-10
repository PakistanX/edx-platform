from django.contrib.auth.models import User
from rest_framework import serializers

from student.models import UserProfile

from .constants import GROUP_TRAINING_MANAGERS, ADMIN, STAFF, TRAINING_MANAGER, LEARNER


class UserSerializer(serializers.ModelSerializer):
    employee_id = serializers.CharField(source='profile.employee_id')
    language = serializers.CharField(source='profile.language')
    name = serializers.CharField(source='get_full_name')
    role = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ('username', 'id', 'email', 'name', 'employee_id', 'language', 'is_active', 'role', 'date_joined', 'last_login')

    def get_role(self, obj):
        if obj.is_superuser:
            return ADMIN
        elif obj.is_staff:
            return STAFF
        elif obj.groups.filter(name=GROUP_TRAINING_MANAGERS).exists():
            return TRAINING_MANAGER

        return LEARNER


class BasicUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('username', 'first_name', 'last_name', 'email', 'password')


class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserProfile
        fields = ('user', 'employee_id', 'language')
