from django.conf import settings
from django.conf.urls import url
from django.views.generic.base import RedirectView

from openedx.features.pakx.lms.overrides.views import (
    AboutUsView,
    BaseTemplateView,
    BusinessView,
    MarketingCampaignPage,
    PartnerWithUsView,
    PrivacyPolicyView,
    PSWRedirectView,
    RefundPolicyView,
    TermsOfUseView,
    basket_checkout,
    checkout_lumsx,
    track_enrollments_lumsx,
    course_about_fiveemodel,
    custom_cap_url_courses,
    custom_cap_url_trainings,
    overview_tab_view,
    partner_space_login,
    switch_space,
    update_lms_tour_status
)

urlpatterns = [
    url(r'^(?P<partner>\w+)/login/$', partner_space_login, name="partner_space_login"),
    url(r'^(?P<space>\w+)/switch_space/$', switch_space, name="switch_space"),
    url(r'^about_us/?$', AboutUsView.as_view(), name="about_us"),
    url(
        r'^courses/{course_id}/overview/$'.format(course_id=settings.COURSE_ID_PATTERN),
        overview_tab_view,
        name='overview_tab_view'
    ),
    url(r'^business/$', BusinessView.as_view(), name='home-business'),
    url(r'^psw/$', PSWRedirectView.as_view(), name='psw'),
    url(r'^terms-of-use/$', TermsOfUseView.as_view(), name='terms-of-use'),
    url(r'^privacy-policy/$', PrivacyPolicyView.as_view(), name='privacy-policy'),
    url(r'^refund-policy/$', RefundPolicyView.as_view(), name='privacy-policy'),
    url(r'^partner-with-us/$', PartnerWithUsView.as_view(), name='partner-with-us'),
    url(r'^workplace-essentials-showcase/$', BusinessView.as_view(), name='we-showcase'),
    url(r'^workplace-harassment/$', MarketingCampaignPage.as_view(), name='workplace-harassment'),
    url(r'^5emodel/signup/$', course_about_fiveemodel, name='5emodel-course-about'),
    url(r'^courses/{}$'.format(r'(?P<course_id>[\w-]+)'), custom_cap_url_courses, name='custom-cap-url-courses'),
    url(r'^trainings/{}$'.format(r'(?P<course_id>[\w-]+)'), custom_cap_url_trainings, name='custom-cap-url-trainings'),
    url(r'^basket_checkout$', basket_checkout, name='basket-checkout'),
    url(r'^checkout_lumsx$', checkout_lumsx, name='checkout-lumsx'),
    # url(r'^track_enrollments_lumsx$', track_enrollments_lumsx, name='track-enrollments-lumsx'),
    url(r'^$', BaseTemplateView.as_view(), name="landing-page"),
    url(r'^update-lms-tour-status/$', update_lms_tour_status, name='update_lms_tour_status'),
    url(r'^ey$', RedirectView.as_view(url='https://discover.ilmx.org/ilmx-for-business/'), name='ilmx-for-business'),
]
