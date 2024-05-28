"""
Commerce URLs
"""


from django.conf import settings
from django.conf.urls import include, url

from . import views

COURSE_URLS = ([
    url(r'^$', views.CourseListView.as_view(), name='list'),
    url(r'^{}/$'.format(settings.COURSE_ID_PATTERN), views.CourseRetrieveUpdateView.as_view(), name='retrieve_update'),
], 'courses')

ORDER_URLS = ([
    url(r'^(?P<number>[-\w]+)/$', views.OrderView.as_view(), name='detail'),
], 'orders')

ENROLLMENT_URLS = ([
    url(
        r'^{}/{}/$'.format(settings.USERNAME_PATTERN, settings.COURSE_ID_PATTERN),
        views.EnrollmentNotification.as_view(),
        name='enrollment-email'
    ),
], 'notify_enroll')

COD_ORDER_URLS = ([
    url(
        r'^{}/{}/(?P<tracking_id>[\d]+)/$'.format(settings.USERNAME_PATTERN, settings.COURSE_ID_PATTERN),
        views.CodOrderNotification.as_view(),
        name='cod-order-email'
    ),
], 'notify_cod_order')

app_name = 'v1'
urlpatterns = [
    url(r'^courses/', include(COURSE_URLS)),
    url(r'^orders/', include(ORDER_URLS)),
    url(r'^enrollment_mail/', include(ENROLLMENT_URLS)),
    url(r'^cod_order_mail/', include(COD_ORDER_URLS)),
]
