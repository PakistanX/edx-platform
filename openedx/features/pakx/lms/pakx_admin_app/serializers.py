from django.contrib.auth.models import User
from rest_framework import serializers
from six import text_type

from student.models import CourseEnrollment
from lms.djangoapps.grades.api import CourseGradeFactory
from .constants import GROUP_TRAINING_MANAGERS, ADMIN, STAFF, TRAINING_MANAGER, LEARNER
from openedx.features.pakx.lms.overrides.utils import get_course_progress_percentage


class UserCourseEnrollmentSerializer(serializers.ModelSerializer):
    display_name = serializers.CharField(source='course.display_name')
    enrollment_status = serializers.CharField(source='mode')
    enrollment_date = serializers.SerializerMethodField()
    progress = serializers.SerializerMethodField()
    grades = serializers.SerializerMethodField()

    class Meta:
        model = CourseEnrollment
        fields = ('display_name', 'enrollment_status', 'enrollment_date', 'progress', 'grades')

    @staticmethod
    def get_enrollment_date(obj):
        return obj.created.strftime('%Y-%m-%d')

    def get_progress(self, obj):
        return get_course_progress_percentage(self.context['request'], text_type(obj.course.id))

    @staticmethod
    def get_grades(obj):
        grades = CourseGradeFactory().read(obj.user, course_key=obj.course.id)
        return {'passed': grades.passed, 'score': grades.percent}


class UserSerializer(serializers.ModelSerializer):
    employee_id = serializers.CharField(source='profile.employee_id')
    language = serializers.CharField(source='profile.language')
    name = serializers.CharField(source='get_full_name')
    role = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ('id', 'email', 'name', 'employee_id', 'language', 'is_active', 'role')

    def get_role(self, obj):
        if obj.is_superuser:
            return ADMIN
        elif obj.is_staff:
            return STAFF
        elif obj.groups.filter(name=GROUP_TRAINING_MANAGERS).exists():
            return TRAINING_MANAGER

        return LEARNER
