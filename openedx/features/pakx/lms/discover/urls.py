from django.conf import settings

from openedx.features.pakx.lms.discover.views import BusinessCoursesView, CoursesListView

urlpatterns = [
    url(r'^courses_data/$', CoursesListView.as_view(), name='course-data-api'),
    url(r'^business_courses_data/$', BusinessCoursesView.as_view(), name='business-data-api'),
]
