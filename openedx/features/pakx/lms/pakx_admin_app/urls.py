from django.conf.urls import url
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import UserProfileViewSet, UserCourseEnrollmentsListAPI

router = DefaultRouter()

router.register('users', UserProfileViewSet, basename='users')


urlpatterns = [
    path('', include(router.urls)),
    url(r'^users/activate/$', UserProfileViewSet.as_view({"post": "activate_users"})),
    url(r'^users/deactivate/$', UserProfileViewSet.as_view({"post": "deactivate_users"})),
    url(r'^user-course-enrollments/(?P<user_id>\d+)/$', UserCourseEnrollmentsListAPI.as_view()),
]
