from django.conf import settings
from django.conf.urls import url

from .views import (
    AboutUsView,
    BusinessView,
    course_about_static,
    switch_space,
    MarketingCampaignPage,
    PartnerWithUsView,
    PrivacyPolicyView,
    TermsOfUseView,
    overview_tab_view,
    partner_space_login,
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
    url(r'^terms-of-use/$', TermsOfUseView.as_view(), name='terms-of-use'),
    url(r'^privacy-policy/$', PrivacyPolicyView.as_view(), name='privacy-policy'),
    url(r'^partner-with-us/$', PartnerWithUsView.as_view(), name='partner-with-us'),
    url(r'^workplace-essentials-showcase/$', BusinessView.as_view(), name='we-showcase'),
    url(r'^workplace-harassment/$', MarketingCampaignPage.as_view(), name='workplace-harassment'),
    url(r'^5emodel/signup/{course_id}$', course_about_static, name='purchase-course'),
]
