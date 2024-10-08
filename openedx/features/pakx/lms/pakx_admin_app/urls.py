"""
URLs patterns for PakX admin app
"""
from django.conf.urls import url
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    AnalyticsStats,
    CourseEnrolmentViewSet,
    CourseListAPI,
    CourseStatsListAPI,
    DownloadCSVView,
    LearnerListAPI,
    UserCourseEnrollmentsListAPI,
    UserInfo,
    UserProfileViewSet,
    UserSearchInputListAPI,
    UserUpdateEnrollmentMode
)

user_viewset_router = DefaultRouter()
user_viewset_router.register('users', UserProfileViewSet, basename='users')

urlpatterns = [
    url(r'^users/activate/$', UserProfileViewSet.as_view({"post": "activate_users"})),
    url(r'^users/deactivate/$', UserProfileViewSet.as_view({"post": "deactivate_users"})),
    url(r'^users/bulk-registration/$', UserProfileViewSet.as_view({"post": "bulk_registration"})),
    url(r'^users/enroll/$', CourseEnrolmentViewSet.as_view({'post': 'enroll_users'})),
    url(r'^user-course-enrollments/(?P<user_id>\d+)/$', UserCourseEnrollmentsListAPI.as_view()),
    url(r'^update-enrollments/(?P<user_id>\d+)/$', UserUpdateEnrollmentMode.as_view()),
    url(r'^userinfo/$', UserInfo.as_view()),
    url(r'^users/search/$', UserSearchInputListAPI.as_view()),
    url(r'^analytics/stats/$', AnalyticsStats.as_view()),
    url(r'^analytics/learners/$', LearnerListAPI.as_view()),
    url(r'^analytics/download$', DownloadCSVView.as_view()),
    url(r'^courses/$', CourseListAPI.as_view()),
    url(r'^courses/stats/$', CourseStatsListAPI.as_view()),
    path('', include(user_viewset_router.urls)),
]
