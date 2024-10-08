"""
Serializer for Admin Panel APIs
"""
from re import match

from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Q
from django.utils.timezone import now
from rest_framework import serializers

from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.user_api.accounts.serializers import LanguageProficiencySerializer
from student.models import CourseEnrollment, LanguageProficiency, Registration, UserProfile

from .constants import GROUP_TRAINING_MANAGERS, LEARNER, ORG_ADMIN, ORG_ROLES, TRAINING_MANAGER
from .utils import specify_user_role


class CourseStatsListSerializer(serializers.ModelSerializer):
    """
    Serializer for list API for courses and its stats
    """
    completion_rate = serializers.SerializerMethodField()
    in_progress = serializers.SerializerMethodField()
    completed = serializers.SerializerMethodField()
    enrolled = serializers.SerializerMethodField()

    class Meta:
        model = CourseOverview
        fields = ('id', 'display_name', 'enrolled', 'completed', 'in_progress', 'completion_rate')

    @staticmethod
    def get_enrolled(obj):
        return obj.completed + obj.in_progress

    @staticmethod
    def get_completion_rate(obj):
        completion_rate = 0 if not obj.completed else (obj.completed / (obj.completed + obj.in_progress)) * 100
        return format(completion_rate, ".2f")

    @staticmethod
    def get_in_progress(obj):
        return obj.in_progress

    @staticmethod
    def get_completed(obj):
        return obj.completed


class UserCourseEnrollmentSerializer(serializers.ModelSerializer):
    """
    Serializer for list API of user course enrollment
    """
    course_id = serializers.CharField(source='course.id')
    display_name = serializers.CharField(source='course.display_name')
    enrollment_status = serializers.CharField(source='mode')
    enrollment_date = serializers.SerializerMethodField()
    completion_date = serializers.SerializerMethodField()
    progress = serializers.SerializerMethodField()
    grades = serializers.SerializerMethodField()
    modes = serializers.SerializerMethodField()

    class Meta:
        model = CourseEnrollment
        fields = (
            'course_id', 'display_name', 'enrollment_status', 'enrollment_date',
            'progress', 'completion_date', 'grades', 'modes'
        )

    @staticmethod
    def get_enrollment_date(obj):
        return obj.created

    @staticmethod
    def get_progress(obj):
        return obj.enrollment_stats.progress if hasattr(obj, 'enrollment_stats') else None

    @staticmethod
    def get_completion_date(obj):
        return obj.enrollment_stats.completion_date if hasattr(obj, 'enrollment_stats') else None

    @staticmethod
    def get_grades(obj):
        return obj.enrollment_stats.grade if hasattr(obj, 'enrollment_stats') else None

    @staticmethod
    def get_modes(obj):
        try:
            modes = obj.course._prefetched_objects_cache['modes']
        except (AttributeError, KeyError):
            return []

        mode_options = []
        now_dt = now()
        for mode in modes:
            if mode.expiration_datetime is None or mode.expiration_datetime >= now_dt:
                mode_options.append(mode.mode_slug)

        return mode_options


class UserDetailViewSerializer(serializers.ModelSerializer):
    """
    Serializer User's object retrieve view
    """
    employee_id = serializers.CharField(source='profile.employee_id')
    name = serializers.CharField(source='profile.name')
    course_enrolled = serializers.SerializerMethodField()
    completed_courses = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ('id', 'username', 'email', 'name', 'employee_id', 'is_active', 'date_joined',
                  'last_login', 'course_enrolled', 'completed_courses')

    @staticmethod
    def get_course_enrolled(obj):
        return obj.completed + obj.in_prog

    @staticmethod
    def get_completed_courses(obj):
        return obj.completed


class UserListingSerializer(serializers.ModelSerializer):
    """
    Serializer User's view-set list view
    """
    employee_id = serializers.CharField(source='profile.employee_id')
    language = serializers.CharField(source='profile.language')
    name = serializers.CharField(source='profile.name')
    role = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ('username', 'id', 'email', 'name', 'employee_id', 'language', 'is_active', 'role')

    @staticmethod
    def get_role(obj):
        if obj.staff_groups:
            return TRAINING_MANAGER if obj.staff_groups[0].name == GROUP_TRAINING_MANAGERS else ORG_ADMIN

        return LEARNER


class UserProfileSerializer(serializers.ModelSerializer):
    language_code = LanguageProficiencySerializer(write_only=True)
    languages = serializers.SerializerMethodField(read_only=True)
    name = serializers.CharField(max_length=30, required=True)

    class Meta:
        model = UserProfile
        fields = ('name', 'employee_id', 'languages', 'language_code', 'organization')

    def validate_name(self, value):
        if not value.strip():
            raise serializers.ValidationError("User's name is required")
        if not match('^[a-zA-Z ]+$', value):
            raise serializers.ValidationError('Only alphabets are allowed')
        return value

    def validate_organization(self, value):
        if not value:
            raise serializers.ValidationError(
                'Organization is required. The person registering new user must have an organization.'
            )
        return value

    def validate_employee_id(self, value):
        if value and not match('^[A-Za-z0-9-_]+$', value):
            raise serializers.ValidationError('Invalid input, only alphanumeric and -_ allowed')
        return value

    def get_languages(self, obj):
        return [{'code': lang.code, 'value': lang.get_code_display()} for lang in obj.language_proficiencies.all()]


class UserSerializer(serializers.ModelSerializer):
    profile = UserProfileSerializer(required=True)
    role = serializers.ChoiceField(choices=ORG_ROLES, write_only=True)
    password = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ('id', 'username', 'email', 'profile', 'role', 'password')
        read_only_fields = ['id']

    def validate_email(self, value):
        if not self.instance and not value.strip():
            raise serializers.ValidationError('This field required!')

        qs = User.objects.filter(email=value)
        if self.instance:
            qs = qs.filter(~Q(id=self.instance.pk))
        if qs.exists():
            raise serializers.ValidationError('Email already exists')

        return value.lower()

    def validate_username(self, value):
        if not self.instance and not value.strip():
            raise serializers.ValidationError('This field required!')

        if len(value) < 2 or len(value) > 30:
            raise serializers.ValidationError('Username must be between 2 and 30 characters long.')

        return value

    @transaction.atomic()
    def create(self, validated_data):
        profile_data = validated_data.pop('profile')
        password = validated_data.pop('password')
        role = validated_data.pop('role')
        validated_data['is_active'] = False

        if profile_data.get('name'):
            f_name, *l_names = profile_data.get('name').split()
            validated_data['first_name'], validated_data['last_name'] = f_name, ' '.join(l_names)

        user = User.objects.create(**validated_data)
        user.set_password(password)
        user.is_active = True
        user.save()

        Registration().register(user)
        specify_user_role(user, role)
        profile_data['user'] = user
        language_code = profile_data.pop('language_code', None)
        profile = UserProfile.objects.create(**profile_data)

        if language_code:
            profile.language_proficiencies.create(code=language_code['code'])

        return user

    @transaction.atomic()
    def update(self, instance, validated_data):
        profile_data = validated_data.pop('profile', None)
        role = validated_data.pop('role', None)

        instance.email = validated_data.get('email', instance.email)
        instance.save(update_fields=['email'])

        if role:
            specify_user_role(instance, role)

        if profile_data:
            profile = instance.profile
            profile.name = profile_data.get('name', profile.name)
            profile.employee_id = profile_data.get('employee_id', profile.employee_id)
            profile.save(update_fields=['name', 'employee_id'])

            language_code = profile_data.pop('language_code', None)
            if language_code:
                LanguageProficiency.objects.filter(user_profile=profile).update(code=language_code['code'])

        return instance


class LearnersSerializer(serializers.ModelSerializer):
    """
    Serializer Learner list view for analytics view list view
    """
    name = serializers.CharField(source='profile.name')
    assigned_courses = serializers.SerializerMethodField()
    incomplete_courses = serializers.SerializerMethodField()
    completed_courses = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            'id', 'username', 'name', 'email', 'last_login',
            'assigned_courses', 'incomplete_courses', 'completed_courses'
        )

    @staticmethod
    def get_assigned_courses(obj):
        return len(obj.enrollment)

    @staticmethod
    def get_incomplete_courses(obj):
        incomplete_course_count = 0
        for stat in obj.enrollment:
            if hasattr(stat, 'enrollment_stats') and stat.enrollment_stats.progress < 100:
                incomplete_course_count += 1
        return incomplete_course_count

    @staticmethod
    def get_completed_courses(obj):
        return len([stat for stat in obj.enrollment if stat.enrollment_stats.progress == 100])


class CoursesSerializer(serializers.ModelSerializer):
    instructor = serializers.SerializerMethodField()

    class Meta:
        model = CourseOverview
        fields = ('id', 'display_name', 'instructor', 'start_date', 'end_date', 'course_image_url')

    def get_instructor(self, obj):
        return self.context['instructors'].get(obj.id) or []
