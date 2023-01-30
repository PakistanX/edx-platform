"""
Urls for custom settings app
"""
from django.conf import settings
from django.conf.urls import url

from .views import CourseCustomSettingsView, ProgramsView, ProgramCreateView, EditProgramView

urlpatterns = [
    url(
        r'^settings/custom/{}$'.format(settings.COURSE_KEY_PATTERN),
        CourseCustomSettingsView.as_view(),
        name='custom_settings'
    ),
    url(r'^programs/{}'.format(r'(?P<program_uuid>[0-9a-f-]+)'), EditProgramView.as_view(), name='edit-program-cms'),
    url(r'^programs/create', ProgramCreateView.as_view(), name='create-program-cms'),
    url(r'^programs$', ProgramsView.as_view(), name='programs-list-cms'),
]
