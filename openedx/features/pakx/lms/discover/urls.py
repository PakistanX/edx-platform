from django.conf.urls import url
from django.urls import path

from openedx.features.pakx.lms.discover.views import BusinessCoursesView, CoursesListView, UserProfileImage

urlpatterns = [
    url(r'^courses_data/$', CoursesListView.as_view(), name='course-data-api'),
    url(r'^business_courses_data/$', BusinessCoursesView.as_view(), name='business-data-api'),
    path('profile_image/<str:username>', UserProfileImage.as_view(), name='user-profile-image'),
]
