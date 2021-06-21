"""
URLs patterns for PakX admin app
"""
from django.conf.urls import url
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import AnalyticsStats, LearnerListAPI, UserCourseEnrollmentsListAPI, UserProfileViewSet

router = DefaultRouter()
router.register('users', UserProfileViewSet, basename='users')

urlpatterns = [
    url(r'^users/activate/$', UserProfileViewSet.as_view({"post": "activate_users"})),
    url(r'^users/deactivate/$', UserProfileViewSet.as_view({"post": "deactivate_users"})),
    url(r'^user-course-enrollments/(?P<user_id>\d+)/$', UserCourseEnrollmentsListAPI.as_view()),
    url(r'^analytics/stats/$', AnalyticsStats.as_view()),
    url(r'^analytics/learners/$', LearnerListAPI.as_view()),
    path('', include(router.urls)),
]
