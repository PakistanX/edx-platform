from django.conf import settings
from django.conf.urls import url

from .views import ContactUsView, business_view, overview_tab_view, partner_with_us_view

urlpatterns = [
    url(r'^support/contact_us/?$', ContactUsView.as_view(), name="contact_us"),
    url(
        r'^courses/{course_id}/overview/$'.format(course_id=settings.COURSE_ID_PATTERN),
        overview_tab_view,
        name='overview_tab_view'
    ),
    url(r'^business/$', business_view, name='home-business'),
    url(r'^partner-with-us/$', partner_with_us_view, name='home-business'),
]
