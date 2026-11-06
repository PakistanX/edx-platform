# -*- coding: utf-8 -*-
"""
Tests for tracked course links: alias slugs / utm sources, hit and unique visitor counting,
the admin-panel API and the public client report page.
"""
import re
from datetime import timedelta
from io import StringIO

from django.conf import settings
from django.contrib import admin
from django.contrib.auth.models import AnonymousUser, Group
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection, transaction
from django.http import Http404, HttpResponse
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from edx_django_utils.cache import RequestCache
from mock import patch
from opaque_keys.edx.keys import CourseKey
from organizations.models import Organization
from rest_framework.test import APIClient
from six import text_type
from waffle.testutils import override_switch

from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.core.djangolib.testing.utils import CacheIsolationTestCase
from openedx.features.pakx.cms.custom_settings.models import PartnerSpace
from openedx.features.pakx.lms.overrides.views import custom_cap_url_courses, custom_cap_url_trainings
from student.tests.factories import UserFactory

from .constants import GROUP_ORGANIZATION_ADMIN
from .course_links import (
    VISITOR_COOKIE,
    VISITOR_RETENTION_DAYS,
    client_stats,
    link_url,
    org_matches,
    parse_report_range,
    today
)
from .models import CourseLink, CourseLinkClient, CourseLinkDailyStat, CourseLinkVisitor

COURSE_KEY = 'course-v1:PSW+TL09+2025'
SLUG = 'business-planning-for-export-success'
BROWSER_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36'
VIEWS = 'openedx.features.pakx.lms.overrides.views'
SWITCH = 'ilmx.course_link_tracking'


class CourseLinkTestCase(CacheIsolationTestCase):
    """Shared fixtures: one course with a canonical slug, one client, one alias and one utm link."""

    def setUp(self):
        super(CourseLinkTestCase, self).setUp()
        self.course = CourseOverviewFactory.create(id=CourseKey.from_string(COURSE_KEY))
        self.client_obj = CourseLinkClient.objects.create(name='Acme')
        self.alias = CourseLink.objects.create(
            client=self.client_obj, course=self.course, kind=CourseLink.ALIAS, value='business-planning-acme'
        )
        self.utm = CourseLink.objects.create(
            client=self.client_obj, course=self.course, kind=CourseLink.UTM, value='acme'
        )
        switch = override_switch(SWITCH, active=True)
        switch.enable()
        self.addCleanup(switch.disable)

    def visit(self, slug, query='', user_agent=BROWSER_UA, cookies=None, method='get', view=custom_cap_url_trainings,
              extra=None):
        """Calls the custom URL view directly; returns (response, mocked about-context builder)."""
        request = getattr(RequestFactory(), method)(
            '/trainings/{}/{}'.format(slug, query), HTTP_USER_AGENT=user_agent, **(extra or {})
        )
        request.COOKIES.update(cookies or {})
        request.user = AnonymousUser()
        with patch(VIEWS + '._get_course_about_context', return_value={}) as about_context, \
                patch(VIEWS + '.render_to_response', side_effect=lambda template, context: HttpResponse('page')):
            response = view(request, slug)
        return response, about_context

    def stat(self, link):
        return CourseLinkDailyStat.objects.filter(link=link, date=today()).first()

    def create_default_partner_space(self):
        """Full-stack requests go through middleware that needs the default partner space to exist."""
        PartnerSpace.objects.create(
            name='ilmx', organization=Organization.objects.create(name='ilmX Space', short_name='ilmx-space')
        )


class CustomUrlResolutionTest(CourseLinkTestCase):
    """Which course a slug serves, and what gets counted."""

    def test_alias_serves_the_linked_course_and_is_counted(self):
        response, about_context = self.visit('business-planning-acme')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(about_context.call_args[0][1], COURSE_KEY)
        self.assertEqual(self.stat(self.alias).hits, 1)

    def test_alias_works_on_the_courses_prefix_too(self):
        response, about_context = self.visit('business-planning-acme', view=custom_cap_url_courses)
        self.assertEqual(about_context.call_args[0][1], COURSE_KEY)
        self.assertEqual(self.stat(self.alias).hits, 1)

    def test_alias_response_points_search_engines_at_the_canonical_page(self):
        response, _ = self.visit('business-planning-acme')
        self.assertIn('rel="canonical"', response['Link'])
        self.assertIn('/trainings/{}/>'.format(SLUG), response['Link'])
        response, _ = self.visit(SLUG)
        self.assertNotIn('Link', response)

    def test_unknown_slug_is_404(self):
        with self.assertRaises(Http404):
            self.visit('no-such-slug')

    def test_inactive_alias_is_404(self):
        CourseLink.objects.filter(pk=self.alias.pk).update(is_active=False)
        with self.assertRaises(Http404):
            self.visit('business-planning-acme')

    def test_canonical_slug_without_utm_counts_nothing(self):
        self.visit(SLUG)
        self.assertEqual(CourseLinkDailyStat.objects.count(), 0)

    def test_registered_utm_source_is_counted_case_insensitively(self):
        self.visit(SLUG, '?utm_source=ACME')
        self.assertEqual(self.stat(self.utm).hits, 1)

    def test_unregistered_utm_source_creates_nothing(self):
        for query in ('?utm_source=other', '?utm_source=', '?utm_source=' + 'x' * 500, '?utm_source=a b;drop'):
            response, _ = self.visit(SLUG, query)
            self.assertEqual(response.status_code, 200)
        self.assertEqual(CourseLinkDailyStat.objects.count(), 0)

    def test_utm_registered_for_another_course_is_not_counted(self):
        other_course = CourseOverviewFactory.create(id=CourseKey.from_string('course-v1:PSW+TL08+2025'))
        CourseLink.objects.filter(pk=self.utm.pk).update(course=other_course)
        self.visit(SLUG, '?utm_source=acme')
        self.assertEqual(CourseLinkDailyStat.objects.count(), 0)

    def test_alias_with_utm_counts_only_the_alias(self):
        self.visit('business-planning-acme', '?utm_source=acme')
        self.assertEqual(self.stat(self.alias).hits, 1)
        self.assertIsNone(self.stat(self.utm))


class VisitCountingTest(CourseLinkTestCase):
    """Raw hits versus unique visitors, and what is not a visit."""

    def test_hits_and_uniques(self):
        response, _ = self.visit('business-planning-acme')
        cookie = response.cookies[VISITOR_COOKIE]
        self.assertTrue(cookie['httponly'])
        visitor = cookie.value
        for _ in range(2):  # same browser again: more hits, same unique visitor
            self.visit('business-planning-acme', cookies={VISITOR_COOKIE: visitor})
        self.visit('business-planning-acme')  # a different browser
        stat = self.stat(self.alias)
        self.assertEqual((stat.hits, stat.unique_visitors), (4, 2))
        self.assertEqual(CourseLinkVisitor.objects.filter(link=self.alias).count(), 2)

    def test_signed_in_and_anonymous_visitors_are_both_counted_without_storing_identity(self):
        signed_in = UserFactory.create()
        request = RequestFactory().get('/trainings/business-planning-acme/', HTTP_USER_AGENT=BROWSER_UA)
        request.user = signed_in
        with patch(VIEWS + '._get_course_about_context', return_value={}), \
                patch(VIEWS + '.render_to_response', side_effect=lambda template, context: HttpResponse('page')):
            custom_cap_url_trainings(request, 'business-planning-acme')
        self.visit('business-planning-acme')  # anonymous
        stat = self.stat(self.alias)
        self.assertEqual((stat.hits, stat.unique_visitors), (2, 2))
        self.assertEqual(
            sorted(f.name for f in CourseLinkVisitor._meta.get_fields() if f.name not in ('id',)),
            ['date', 'link', 'visitor_id'],  # no user or IP column
        )

    def test_malformed_visitor_cookie_is_replaced(self):
        response, _ = self.visit('business-planning-acme', cookies={VISITOR_COOKIE: 'not-a-visitor-id'})
        self.assertRegexpMatches(response.cookies[VISITOR_COOKIE].value, r'^[0-9a-f]{32}$')

    def test_cubot_phone_is_a_real_visitor(self):
        cubot = 'Mozilla/5.0 (Linux; Android 10; CUBOT X30) Chrome/110'
        response, _ = self.visit('business-planning-acme', user_agent=cubot)
        self.assertIn(VISITOR_COOKIE, response.cookies)
        self.assertEqual(self.stat(self.alias).hits, 1)

    def test_bots_previews_and_head_requests_are_not_counted(self):
        for user_agent in (
                'Googlebot/2.1', 'WhatsApp/2.23.1', 'TelegramBot (like TwitterBot)', 'Slackbot-LinkExpanding 1.0',
                'curl/7.79', 'python-requests/2.25', '',
        ):
            response, _ = self.visit('business-planning-acme', user_agent=user_agent)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(VISITOR_COOKIE, response.cookies)
        self.visit('business-planning-acme', method='head')
        self.assertEqual(CourseLinkDailyStat.objects.count(), 0)

    def test_nothing_is_counted_while_the_switch_is_off(self):
        with override_switch(SWITCH, active=False):
            RequestCache.clear_all_namespaces()  # waffle caches the switch value per request
            response, _ = self.visit('business-planning-acme')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(CourseLinkDailyStat.objects.count(), 0)

    def test_page_still_renders_when_the_utm_lookup_fails(self):
        lookup = 'openedx.features.pakx.lms.pakx_admin_app.course_links.match_utm_link'
        with patch(lookup, side_effect=RuntimeError('db down')):
            response, _ = self.visit(SLUG, '?utm_source=acme')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(VISITOR_COOKIE, response.cookies)

    def test_page_still_renders_when_counting_fails(self):
        with patch('openedx.features.pakx.lms.pakx_admin_app.course_links.CourseLinkDailyStat.objects') as stats:
            stats.filter.side_effect = RuntimeError('db down')
            response, _ = self.visit('business-planning-acme')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(VISITOR_COOKIE, response.cookies)

    def test_stats_report_distinct_visitors_across_days(self):
        visitor = 'a' * 32
        yesterday = today() - timedelta(days=1)
        CourseLinkDailyStat.objects.create(link=self.alias, date=yesterday, hits=3, unique_visitors=2)
        CourseLinkVisitor.objects.create(link=self.alias, visitor_id=visitor, date=yesterday)
        CourseLinkVisitor.objects.create(link=self.alias, visitor_id='b' * 32, date=yesterday)
        self.visit('business-planning-acme', cookies={VISITOR_COOKIE: visitor})  # returns today
        stats = client_stats(self.client_obj, *parse_report_range(days=2))
        self.assertEqual((stats['hits'], stats['unique_visitors']), (4, 2))  # 'a' counted once overall
        self.assertEqual([(d['hits'], d['unique_visitors']) for d in stats['series']], [(3, 2), (1, 1)])
        link = [item for item in stats['links'] if item['id'] == self.alias.pk][0]
        self.assertEqual((link['hits'], link['unique_visitors']), (4, 2))
        self.assertTrue(link['url'].endswith('/trainings/business-planning-acme/'))

    def test_report_range_validation(self):
        for kwargs in ({'days': 0}, {'days': 400}, {'date_from': '2026-02-01', 'date_to': '2026-01-01'},
                       {'date_from': 'nonsense'}):
            with self.assertRaises(ValueError):
                parse_report_range(**kwargs)


class CourseLinkApiTest(CourseLinkTestCase):
    """Admin-panel API: scoping per client organization and link validation."""

    def setUp(self):
        super(CourseLinkApiTest, self).setUp()
        self.create_default_partner_space()
        self.org = Organization.objects.create(name='Acme Org', short_name='acme')
        self.other_org = Organization.objects.create(name='Globex Org', short_name='globex')
        CourseLinkClient.objects.filter(pk=self.client_obj.pk).update(organization=self.org)
        self.other_client = CourseLinkClient.objects.create(name='Globex', organization=self.other_org)
        self.staff = UserFactory.create(is_staff=True)
        self.acme_admin = self._org_admin(self.org)
        self.globex_admin = self._org_admin(self.other_org)
        CourseOverview.objects.filter(pk=self.course.pk).update(org='acme')  # the course belongs to Acme
        self.course.refresh_from_db()
        self.api = APIClient()

    def _org_admin(self, org):
        user = UserFactory.create()
        user.profile.organization = org
        user.profile.save()
        user.groups.add(Group.objects.get_or_create(name=GROUP_ORGANIZATION_ADMIN)[0])
        return user

    def _ids(self, response):
        return sorted(item['id'] for item in response.json())

    def test_org_admin_sees_only_own_client_and_links(self):
        self.api.force_authenticate(self.acme_admin)
        self.assertEqual(self._ids(self.api.get('/adminpanel/course-link-clients/')), [self.client_obj.pk])
        self.assertEqual(self._ids(self.api.get('/adminpanel/course-links/')), sorted([self.alias.pk, self.utm.pk]))
        other = '/adminpanel/course-link-clients/{}/'.format(self.other_client.pk)
        self.assertEqual(self.api.get(other).status_code, 404)
        self.assertEqual(self.api.get(other + 'stats/').status_code, 404)

    def test_staff_sees_all_clients(self):
        self.api.force_authenticate(self.staff)
        self.assertEqual(self._ids(self.api.get('/adminpanel/course-link-clients/')),
                         sorted([self.client_obj.pk, self.other_client.pk]))

    def test_staff_can_narrow_to_one_organization(self):
        self.api.force_authenticate(self.staff)
        response = self.api.get('/adminpanel/course-link-clients/?org=globex')
        self.assertEqual(self._ids(response), [self.other_client.pk])
        self.assertEqual(self._ids(self.api.get('/adminpanel/course-link-clients/?org=no-such-org')),
                         sorted([self.client_obj.pk, self.other_client.pk]))
        self.api.force_authenticate(self.acme_admin)  # course staff cannot widen or switch scope
        self.assertEqual(self._ids(self.api.get('/adminpanel/course-link-clients/?org=globex')), [self.client_obj.pk])

    def test_anonymous_and_unrelated_users_are_refused(self):
        self.assertIn(self.api.get('/adminpanel/course-links/').status_code, (401, 403))
        self.api.force_authenticate(UserFactory.create())
        self.assertEqual(self.api.get('/adminpanel/course-links/').status_code, 403)

    def test_org_admin_cannot_write(self):
        self.api.force_authenticate(self.acme_admin)
        payload = {'client': self.client_obj.pk, 'course': COURSE_KEY, 'kind': 'utm', 'value': 'new-source'}
        self.assertEqual(self.api.post('/adminpanel/course-links/', payload, format='json').status_code, 403)
        response = self.api.post('/adminpanel/course-link-clients/', {'name': 'X'}, format='json')
        self.assertEqual(response.status_code, 403)
        client_url = '/adminpanel/course-link-clients/{}/'.format(self.client_obj.pk)
        self.assertEqual(self.api.patch(client_url, {'is_report_enabled': False}, format='json').status_code, 403)
        self.assertEqual(self.api.post(client_url + 'regenerate_token/').status_code, 403)
        self.assertEqual(self.api.patch('/adminpanel/course-links/{}/'.format(self.alias.pk), {'is_active': False},
                                        format='json').status_code, 403)

    def test_staff_cannot_link_a_course_of_another_organization(self):
        CourseOverview.objects.filter(pk=self.course.pk).update(org='globex')
        payload = {'client': self.client_obj.pk, 'course': COURSE_KEY, 'kind': 'utm', 'value': 'new-source'}
        self.api.force_authenticate(self.staff)
        response = self.api.post('/adminpanel/course-links/', payload, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('does not belong', response.json()['course'][0])

    def test_organization_match_is_anchored_and_clients_without_one_are_unrestricted(self):
        short = Organization.objects.create(name='Ac', short_name='ac')  # a prefix of the course's org "acme"
        prefix_client = CourseLinkClient.objects.create(name='Prefix', organization=short)
        free_client = CourseLinkClient.objects.create(name='Free')
        self.api.force_authenticate(self.staff)
        payload = {'course': COURSE_KEY, 'kind': 'utm', 'value': 'src'}
        refused = self.api.post('/adminpanel/course-links/', dict(payload, client=prefix_client.pk), format='json')
        self.assertEqual(refused.status_code, 400)
        allowed = self.api.post('/adminpanel/course-links/', dict(payload, client=free_client.pk), format='json')
        self.assertEqual(allowed.status_code, 201, allowed.content)

    def test_stats_include_previous_period_totals(self):
        CourseLinkDailyStat.objects.create(
            link=self.alias, date=today() - timedelta(days=40), hits=4, unique_visitors=2
        )
        CourseLinkVisitor.objects.create(link=self.alias, visitor_id='d' * 32, date=today() - timedelta(days=40))
        self.api.force_authenticate(self.staff)
        data = self.api.get('/adminpanel/course-link-clients/{}/stats/?days=30'.format(self.client_obj.pk)).json()
        self.assertEqual(data['previous']['hits'], 4)
        self.assertEqual(data['previous']['unique_visitors'], 1)
        self.assertIn('generated_at', data)

    def test_stats_endpoint(self):
        CourseLinkDailyStat.objects.create(link=self.alias, date=today(), hits=5, unique_visitors=3)
        self.api.force_authenticate(self.acme_admin)
        data = self.api.get('/adminpanel/course-link-clients/{}/stats/?days=7'.format(self.client_obj.pk)).json()
        self.assertEqual(data['hits'], 5)
        self.assertEqual(len(data['series']), 7)
        self.assertTrue(data['tracking_enabled'])
        self.assertIn('/course-link-report/', data['report_url'])
        bad = self.api.get('/adminpanel/course-link-clients/{}/stats/?days=9999'.format(self.client_obj.pk))
        self.assertEqual(bad.status_code, 400)

    def test_staff_creates_alias_and_utm_links(self):
        self.api.force_authenticate(self.staff)
        alias = self.api.post('/adminpanel/course-links/', {
            'client': self.client_obj.pk, 'course': COURSE_KEY, 'kind': 'alias', 'value': ' Business-Planning-Beta '
        }, format='json')
        self.assertEqual(alias.status_code, 201, alias.content)
        self.assertEqual(alias.json()['value'], 'business-planning-beta')
        self.assertTrue(alias.json()['url'].endswith('/trainings/business-planning-beta/'))
        utm = self.api.post('/adminpanel/course-links/', {
            'client': self.client_obj.pk, 'course': COURSE_KEY, 'kind': 'utm', 'value': 'acme-newsletter'
        }, format='json')
        self.assertEqual(utm.status_code, 201, utm.content)
        self.assertTrue(utm.json()['url'].endswith('/trainings/{}/?utm_source=acme-newsletter'.format(SLUG)))
        self.assertEqual(self.api.get('/adminpanel/course-links/?client={}'.format(self.other_client.pk)).json(), [])

    def test_link_validation(self):
        self.api.force_authenticate(self.staff)
        base = {'client': self.client_obj.pk, 'course': COURSE_KEY}
        cases = (
            (dict(base, kind='alias', value=SLUG), 'value'),  # canonical slug
            (dict(base, kind='alias', value='business-planning-acme'), 'value'),  # duplicate alias
            (dict(base, kind='alias', value='has space'), 'value'),
            (dict(base, kind='alias', value='x'), 'value'),
            (dict(base, kind='alias', value='under_score'), 'value'),
            (dict(base, kind='utm', value='acme'), 'value'),  # duplicate utm for the course
            (dict(base, kind='utm', value='bad/source'), 'value'),
            (dict(base, kind='alias', value='fine-alias', course='course-v1:No+Such+Course'), 'course'),
            (dict(base, kind='alias', value='fine-alias', course='garbage'), 'course'),
        )
        for payload, field in cases:
            response = self.api.post('/adminpanel/course-links/', payload, format='json')
            self.assertEqual(response.status_code, 400, payload)
            self.assertIn(field, response.json(), payload)

    def test_course_without_custom_url_is_refused(self):
        CourseOverviewFactory.create(id=CourseKey.from_string('course-v1:Demo+NoSlug+2025'))
        self.api.force_authenticate(self.staff)
        response = self.api.post('/adminpanel/course-links/', {
            'client': self.client_obj.pk, 'course': 'course-v1:Demo+NoSlug+2025', 'kind': 'alias', 'value': 'demo-alias'
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('course', response.json())

    def test_patch_only_toggles_is_active(self):
        self.api.force_authenticate(self.staff)
        response = self.api.patch('/adminpanel/course-links/{}/'.format(self.alias.pk),
                                  {'is_active': False, 'value': 'changed', 'kind': 'utm'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.alias.refresh_from_db()
        self.assertEqual((self.alias.is_active, self.alias.value, self.alias.kind),
                         (False, 'business-planning-acme', CourseLink.ALIAS))
        self.assertEqual(self.api.delete('/adminpanel/course-links/{}/'.format(self.alias.pk)).status_code, 405)

    def test_model_clean_enforces_the_api_rules_for_django_admin(self):
        other_course = CourseOverviewFactory.create(id=CourseKey.from_string('course-v1:PSW+TL08+2025'))
        for kind, value, course in (
                (CourseLink.ALIAS, 'business-planning-acme', other_course),  # alias already used by another course
                (CourseLink.ALIAS, SLUG, self.course),  # canonical slug
                (CourseLink.ALIAS, 'x', self.course),  # malformed
                (CourseLink.UTM, 'acme', self.course),  # duplicate utm for the course
        ):
            link = CourseLink(client=self.client_obj, course=course, kind=kind, value=value)
            with self.assertRaises(ValidationError, msg=value):
                link.full_clean()
        link = CourseLink(client=self.client_obj, course=self.course, kind=CourseLink.UTM, value=' NewSource ')
        link.full_clean()
        self.assertEqual(link.value, 'newsource')

    def test_client_list_counts_links_without_a_query_per_client(self):
        self.api.force_authenticate(self.staff)
        for index in range(3):
            CourseLinkClient.objects.create(name='Extra {}'.format(index))
        with CaptureQueriesContext(connection) as queries:
            response = self.api.get('/adminpanel/course-link-clients/')
        counts = {item['name']: item['link_count'] for item in response.json()}
        self.assertEqual(counts['Acme'], 2)
        self.assertEqual(counts['Extra 0'], 0)
        self.assertFalse([q for q in queries.captured_queries if 'COUNT' in q['sql'] and 'courselink' in q['sql']
                          and q['sql'].count('GROUP BY') == 0])

    def test_regenerate_token_invalidates_the_old_report_url(self):
        old_token = self.client_obj.report_token
        self.api.force_authenticate(self.staff)
        response = self.api.post('/adminpanel/course-link-clients/{}/regenerate_token/'.format(self.client_obj.pk))
        self.assertEqual(response.status_code, 200)
        self.client_obj.refresh_from_db()
        self.assertNotEqual(self.client_obj.report_token, old_token)
        self.assertEqual(self.client.get('/course-link-report/{}/'.format(old_token)).status_code, 404)


class ClientReportPageTest(CourseLinkTestCase):
    """Public, token-guarded report page."""

    def setUp(self):
        super(ClientReportPageTest, self).setUp()
        self.create_default_partner_space()

    def url(self, token=None, query=''):
        return '/course-link-report/{}/{}'.format(token or self.client_obj.report_token, query)

    def test_report_page_shows_totals_and_is_not_indexable(self):
        CourseLinkDailyStat.objects.create(link=self.alias, date=today(), hits=7, unique_visitors=4)
        CourseLinkVisitor.objects.create(link=self.alias, visitor_id='c' * 32, date=today())
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        body = response.content.decode('utf-8')
        self.assertIn('Acme', body)
        self.assertIn('<b>7</b><span>Total hits</span>', body)
        self.assertIn('business-planning-acme', body)
        self.assertIn('noindex', response['X-Robots-Tag'])
        self.assertIn('no-store', response['Cache-Control'])

    def test_unknown_malformed_or_disabled_token_is_404(self):
        self.assertEqual(self.client.get(self.url('0' * 8 + '-0000-0000-0000-' + '0' * 12)).status_code, 404)
        self.assertEqual(self.client.get('/course-link-report/' + 'a' * 32 + '/').status_code, 404)
        CourseLinkClient.objects.filter(pk=self.client_obj.pk).update(is_report_enabled=False)
        self.assertEqual(self.client.get(self.url()).status_code, 404)

    def test_custom_range_previous_period_delta_and_updated_time(self):
        CourseLinkDailyStat.objects.create(
            link=self.alias, date=today() - timedelta(days=40), hits=2, unique_visitors=1
        )
        CourseLinkVisitor.objects.create(link=self.alias, visitor_id='c' * 32, date=today() - timedelta(days=40))
        self.visit('business-planning-acme')
        response = self.client.get(self.url(query='?days=30'))
        body = response.content.decode('utf-8')
        self.assertIn('vs previous period', body)
        self.assertIn('updated ', body)
        start, end = today() - timedelta(days=5), today()
        custom = self.client.get(self.url(query='?from={}&to={}'.format(start, end)))
        self.assertEqual(custom.status_code, 200)
        self.assertIn('from={}&amp;to={}&amp;format=csv'.format(start, end), custom.content.decode('utf-8'))
        self.assertEqual(self.client.get(self.url(query='?from=2026-02-01&to=2026-01-01')).status_code, 400)

    def test_ranges_beyond_the_kept_visitor_rows_are_refused_or_not_compared(self):
        too_old = today() - timedelta(days=VISITOR_RETENTION_DAYS + 5)
        response = self.client.get(self.url(query='?from={}&to={}'.format(too_old, too_old + timedelta(days=10))))
        self.assertEqual(response.status_code, 400)
        with self.assertRaises(ValueError):
            parse_report_range(date_from=text_type(too_old), date_to=text_type(today()))
        start = today() - timedelta(days=VISITOR_RETENTION_DAYS - 3)  # allowed, but the period before it is not
        stats = client_stats(self.client_obj, start, start + timedelta(days=10))
        self.assertIsNone(stats['previous'])
        body = self.client.get(self.url(query='?from={}&to={}'.format(start, start + timedelta(days=10)))).content
        self.assertNotIn('vs previous period', body.decode('utf-8'))
        recent = client_stats(self.client_obj, today() - timedelta(days=5), today(), with_previous=False)
        self.assertIsNone(recent['previous'])

    def test_tracking_notice_only_while_the_switch_is_off(self):
        self.assertNotIn('tracking is currently paused', self.client.get(self.url()).content.decode('utf-8'))
        with override_switch(SWITCH, active=False):
            RequestCache.clear_all_namespaces()
            self.assertIn('tracking is currently paused', self.client.get(self.url()).content.decode('utf-8'))

    def test_csv_download_and_bad_range(self):
        CourseLinkDailyStat.objects.create(link=self.utm, date=today(), hits=2, unique_visitors=1)
        response = self.client.get(self.url(query='?days=7&format=csv'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8')
        lines = response.content.decode('utf-8').strip().splitlines()
        self.assertEqual(
            lines[0],
            'date,link_type,link_value,utm_medium,utm_campaign,utm_term,utm_content,course,hits,unique_visitors',
        )
        self.assertIn('{},utm,acme,,,,,{},2,1'.format(today(), COURSE_KEY), lines)
        self.assertEqual(len(lines), 1 + 2 * 7)  # header + 7 days for each of the 2 links
        self.assertEqual(self.client.get(self.url(query='?days=abc')).status_code, 400)
        self.assertEqual(self.client.get(self.url(query='?days=0')).status_code, 400)


class PruneVisitorsCommandTest(CourseLinkTestCase):
    """Retention of per-visitor rows."""

    def test_prunes_only_old_visitor_rows_and_keeps_daily_stats(self):
        old_day, recent_day = today() - timedelta(days=734), today() - timedelta(days=30)
        CourseLinkVisitor.objects.create(link=self.alias, visitor_id='a' * 32, date=old_day)
        CourseLinkVisitor.objects.create(link=self.alias, visitor_id='b' * 32, date=recent_day)
        CourseLinkDailyStat.objects.create(link=self.alias, date=old_day, hits=9, unique_visitors=5)
        out = StringIO()
        call_command('prune_course_link_visitors', '--dry-run', stdout=out)
        self.assertIn('Would delete 1', out.getvalue())
        self.assertEqual(CourseLinkVisitor.objects.count(), 2)
        call_command('prune_course_link_visitors', stdout=out)
        self.assertEqual(list(CourseLinkVisitor.objects.values_list('visitor_id', flat=True)), ['b' * 32])
        self.assertEqual(CourseLinkDailyStat.objects.get(link=self.alias, date=old_day).hits, 9)

    def test_refuses_a_retention_shorter_than_the_report_cap(self):
        with self.assertRaises(CommandError):
            call_command('prune_course_link_visitors', '--days', '30')


class CountingGatesAndAdminDetailsTest(CourseLinkTestCase):
    """Concurrency, gating, admin columns and report-page details."""

    def setUp(self):
        super(CountingGatesAndAdminDetailsTest, self).setUp()
        self.create_default_partner_space()
        self.staff = UserFactory.create(is_staff=True, is_superuser=True)
        self.api = APIClient()

    def test_prefetch_and_preview_purpose_headers_are_not_counted(self):
        for header in ('HTTP_PURPOSE', 'HTTP_X_PURPOSE', 'HTTP_SEC_PURPOSE', 'HTTP_SEC_FETCH_PURPOSE'):
            for value in ('prefetch', 'preview', 'prefetch;prerender'):
                response, _ = self.visit('business-planning-acme', extra={header: value})
                self.assertNotIn(VISITOR_COOKIE, response.cookies)
        self.assertEqual(CourseLinkDailyStat.objects.count(), 0)

    def test_utm_lookup_only_runs_for_countable_requests_with_tracking_on(self):
        lookup = 'openedx.features.pakx.lms.pakx_admin_app.course_links.match_utm_link'
        with patch(lookup, return_value=None) as match:
            self.visit(SLUG)  # no utm_source at all
            self.visit(SLUG, '?utm_source=acme', user_agent='Googlebot/2.1')
            self.assertFalse(match.called)
            with override_switch(SWITCH, active=False):
                RequestCache.clear_all_namespaces()
                self.visit(SLUG, '?utm_source=acme')
            self.assertFalse(match.called)
            RequestCache.clear_all_namespaces()
            self.visit(SLUG, '?utm_source=acme')
            self.assertTrue(match.called)

    def test_a_lost_race_on_the_day_row_still_counts_without_raising(self):
        with patch.object(CourseLinkDailyStat.objects, 'create', side_effect=IntegrityError):
            response, _ = self.visit('business-planning-acme')
        self.assertEqual(response.status_code, 200)
        self.assertIn(VISITOR_COOKIE, response.cookies)

    def test_day_row_is_created_once_and_then_updated_in_place(self):
        for _ in range(3):
            self.visit('business-planning-acme')
        self.assertEqual(CourseLinkDailyStat.objects.filter(link=self.alias).count(), 1)
        self.assertEqual(self.stat(self.alias).hits, 3)

    def test_future_end_date_is_clamped_and_a_future_only_range_refused(self):
        start, end = parse_report_range(date_from=text_type(today() - timedelta(days=3)),
                                        date_to=text_type(today() + timedelta(days=30)))
        self.assertEqual(end, today())
        with self.assertRaises(ValueError):
            parse_report_range(date_from=text_type(today() + timedelta(days=2)),
                               date_to=text_type(today() + timedelta(days=5)))

    def test_alias_is_unique_in_the_database_and_the_api_reports_a_lost_race(self):
        self.assertEqual(self.alias.alias_slug, 'business-planning-acme')
        self.assertIsNone(self.utm.alias_slug)
        other_course = CourseOverviewFactory.create(id=CourseKey.from_string('course-v1:PSW+TL08+2025'))
        with self.assertRaises(IntegrityError), transaction.atomic():
            CourseLink.objects.create(
                client=self.client_obj, course=other_course, kind=CourseLink.ALIAS, value='Business-Planning-Acme'
            )
        self.api.force_authenticate(self.staff)
        with patch('openedx.features.pakx.lms.pakx_admin_app.course_link_api.link_errors', return_value={}):
            response = self.api.post('/adminpanel/course-links/', {
                'client': self.client_obj.pk, 'course': COURSE_KEY, 'kind': 'alias', 'value': 'business-planning-acme',
            }, format='json')
        self.assertEqual(response.status_code, 400)

    def test_visitor_table_keys_serve_the_report_and_the_prune(self):
        # the unique key doubles as the report's index, so there is no second index over the same columns
        self.assertEqual(CourseLinkVisitor._meta.unique_together, (('link', 'date', 'visitor_id'),))
        self.assertEqual([tuple(index.fields) for index in CourseLinkVisitor._meta.indexes], [('date',)])

    def test_report_url_is_none_while_the_report_is_off(self):
        CourseLinkClient.objects.filter(pk=self.client_obj.pk).update(is_report_enabled=False)
        self.api.force_authenticate(self.staff)
        stats = self.api.get('/adminpanel/course-link-clients/{}/stats/'.format(self.client_obj.pk)).json()
        self.assertIsNone(stats['report_url'])
        self.assertIsNone(self.api.get('/adminpanel/course-link-clients/').json()[0]['report_url'])

    def test_model_string_forms(self):
        self.assertEqual(str(self.alias), 'Alias: business-planning-acme -> {}'.format(COURSE_KEY))
        self.assertIn('Acme', str(self.client_obj))
        self.assertIn('hits=0', str(CourseLinkDailyStat(link=self.alias, date=today())))

    def test_django_admin_columns_and_nothing_can_be_deleted(self):
        request = RequestFactory().get('/admin/')
        request.user = self.staff
        client_admin = admin.site._registry[CourseLinkClient]
        link_admin = admin.site._registry[CourseLink]
        stat_admin = admin.site._registry[CourseLinkDailyStat]
        report = client_admin.report_url(self.client_obj)
        self.assertIn('<a href="http', report)  # the report page does not count visits, so it is clickable
        self.assertEqual(client_admin.report_url(CourseLinkClient()), '-')
        CourseLinkClient.objects.filter(pk=self.client_obj.pk).update(is_report_enabled=False)
        self.client_obj.refresh_from_db()
        self.assertEqual(client_admin.report_url(self.client_obj), '-')
        inline = client_admin.inlines[0](CourseLinkClient, admin.site)
        for url in (link_admin.url(self.alias), inline.url(self.alias)):
            self.assertTrue(url.startswith('http'))  # plain text: a click from the admin would count as a visit
            self.assertNotIn('<a', url)
        self.assertEqual(link_admin.url(CourseLink()), '-')
        self.assertEqual(inline.url(CourseLink()), '-')
        for model_admin in (client_admin, link_admin, stat_admin):
            self.assertFalse(model_admin.has_delete_permission(request))
        self.assertFalse(inline.can_delete)
        self.assertFalse(stat_admin.has_add_permission(request))

    def test_report_page_does_not_link_the_tracked_urls(self):
        body = self.client.get('/course-link-report/{}/'.format(self.client_obj.report_token)).content.decode('utf-8')
        self.assertNotIn('<a href="http://testserver', body)
        self.assertIn('title="{}"'.format(COURSE_KEY), body)  # hover shows the course id
        self.assertIn('data-url="', body)
        self.assertIn('class="copy"', body)

    def test_the_secret_report_url_is_not_written_to_the_tracking_log(self):
        path = '/course-link-report/{}/'.format(self.client_obj.report_token)
        self.assertTrue(any(re.match(pattern, path) for pattern in settings.TRACKING_IGNORE_URL_PATTERNS))

    def test_malformed_token_that_matches_the_url_pattern_is_404(self):
        self.assertEqual(self.client.get('/course-link-report/' + '-' * 32 + '/').status_code, 404)


class MultiParameterUtmTest(CourseLinkTestCase):
    """utm_medium / utm_campaign / utm_term / utm_content on top of utm_source."""

    def setUp(self):
        super(MultiParameterUtmTest, self).setUp()
        self.create_default_partner_space()
        self.staff = UserFactory.create(is_staff=True)
        self.api = APIClient()
        self.email = CourseLink.objects.create(
            client=self.client_obj, course=self.course, kind=CourseLink.UTM, value='acme', utm_medium='email'
        )
        self.spring = CourseLink.objects.create(
            client=self.client_obj, course=self.course, kind=CourseLink.UTM, value='acme', utm_medium='email',
            utm_campaign='Spring '.strip().upper()
        )

    def test_values_are_normalised_and_labelled(self):
        self.assertEqual(self.spring.utm_campaign, 'spring')
        self.assertEqual(self.spring.label, 'UTM: acme / email / spring')
        self.assertEqual(self.utm.label, 'UTM: acme')
        self.assertEqual(self.alias.label, 'Alias: business-planning-acme')

    def test_url_carries_every_filled_parameter_in_order(self):
        self.assertTrue(
            link_url(self.spring).endswith('/trainings/{}/?utm_source=acme&utm_medium=email&utm_campaign=spring'.format(
                SLUG
            ))
        )
        self.assertTrue(link_url(self.utm).endswith('?utm_source=acme'))

    def test_the_most_specific_matching_link_gets_the_hit(self):
        self.visit(SLUG, '?utm_source=acme&utm_medium=email&utm_campaign=spring&utm_content=banner')
        self.assertEqual(self.stat(self.spring).hits, 1)
        self.assertIsNone(self.stat(self.email))
        self.assertIsNone(self.stat(self.utm))

    def test_fewer_parameters_fall_back_to_the_less_specific_link(self):
        self.visit(SLUG, '?utm_source=acme&utm_medium=email')
        self.visit(SLUG, '?utm_source=acme&utm_medium=social')  # medium matches no medium-specific link
        self.visit(SLUG, '?utm_source=acme')
        self.assertEqual(self.stat(self.email).hits, 1)
        self.assertEqual(self.stat(self.utm).hits, 2)
        self.assertIsNone(self.stat(self.spring))

    def test_values_are_matched_case_insensitively_and_junk_is_ignored(self):
        self.visit(SLUG, '?utm_source=ACME&utm_medium=EMAIL&utm_campaign=%20SPRING')
        self.visit(SLUG, '?utm_source=acme&utm_medium=email&utm_campaign=' + 'x' * 80)  # too long: ignored
        self.assertEqual(self.stat(self.spring).hits, 1)
        self.assertEqual(self.stat(self.email).hits, 1)

    def test_extra_parameters_alone_without_a_source_count_nothing(self):
        self.visit(SLUG, '?utm_medium=email&utm_campaign=spring')
        self.assertEqual(CourseLinkDailyStat.objects.count(), 0)

    def test_unique_combination_per_course(self):
        duplicate = CourseLink(
            client=self.client_obj, course=self.course, kind=CourseLink.UTM, value='acme', utm_medium='EMAIL'
        )
        with self.assertRaises(ValidationError):
            duplicate.full_clean()
        different = CourseLink(
            client=self.client_obj, course=self.course, kind=CourseLink.UTM, value='acme', utm_medium='email',
            utm_term='shoes',
        )
        different.full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            CourseLink.objects.create(
                client=self.client_obj, course=self.course, kind=CourseLink.UTM, value='acme', utm_medium='email'
            )

    def test_aliases_cannot_carry_utm_parameters(self):
        link = CourseLink(
            client=self.client_obj, course=self.course, kind=CourseLink.ALIAS, value='some-new-alias',
            utm_campaign='spring',
        )
        with self.assertRaises(ValidationError):
            link.full_clean()

    def test_api_creates_validates_and_returns_the_parameters(self):
        self.api.force_authenticate(self.staff)
        payload = {
            'client': self.client_obj.pk, 'course': COURSE_KEY, 'kind': 'utm', 'value': 'acme',
            'utm_medium': 'Social', 'utm_campaign': 'launch-2026', 'utm_content': 'banner',
        }
        response = self.api.post('/adminpanel/course-links/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()
        self.assertEqual((data['utm_medium'], data['utm_campaign'], data['utm_content']),
                         ('social', 'launch-2026', 'banner'))
        self.assertEqual(data['label'], 'UTM: acme / social / launch-2026 / banner')
        self.assertTrue(data['url'].endswith(
            '?utm_source=acme&utm_medium=social&utm_campaign=launch-2026&utm_content=banner'
        ))
        self.assertEqual(self.api.post('/adminpanel/course-links/', payload, format='json').status_code, 400)
        for bad in ({'utm_medium': 'has space'}, {'utm_term': 'x' * 51}, {'utm_campaign': '-lead'}):
            response = self.api.post(
                '/adminpanel/course-links/', dict(payload, utm_content='other', **bad), format='json'
            )
            self.assertEqual(response.status_code, 400, bad)
        alias = self.api.post('/adminpanel/course-links/', {
            'client': self.client_obj.pk, 'course': COURSE_KEY, 'kind': 'alias', 'value': 'fresh-alias',
            'utm_medium': 'email',
        }, format='json')
        self.assertEqual(alias.status_code, 400)

    def test_patch_cannot_change_the_parameters(self):
        self.api.force_authenticate(self.staff)
        response = self.api.patch(
            '/adminpanel/course-links/{}/'.format(self.spring.pk), {'is_active': False, 'utm_medium': 'x'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.spring.refresh_from_db()
        self.assertEqual((self.spring.utm_medium, self.spring.is_active), ('email', False))

    def test_report_labels_and_csv_include_the_parameters(self):
        CourseLinkDailyStat.objects.create(link=self.spring, date=today(), hits=3, unique_visitors=2)
        url = '/course-link-report/{}/'.format(self.client_obj.report_token)
        body = self.client.get(url).content.decode('utf-8')
        self.assertIn('UTM: acme / email / spring', body)
        self.assertIn('utm_campaign=spring', body)
        lines = self.client.get(url + '?days=7&format=csv').content.decode('utf-8').splitlines()
        self.assertIn('{},utm,acme,email,spring,,,{},3,2'.format(today(), COURSE_KEY), lines)


class OwnershipAndAdminEditsTest(CourseLinkTestCase):
    """Cross-client matching, deleted courses, admin edits and the zero-visit report page."""

    def setUp(self):
        super(OwnershipAndAdminEditsTest, self).setUp()
        self.create_default_partner_space()
        self.staff = UserFactory.create(is_staff=True, is_superuser=True)
        self.api = APIClient()
        self.other_client = CourseLinkClient.objects.create(name='Globex')
        self.request = RequestFactory().get('/admin/')
        self.request.user = self.staff

    def make_utm(self, client=None, **extras):
        return CourseLink.objects.create(
            client=client or self.client_obj, course=self.course, kind=CourseLink.UTM, value='news', **extras
        )

    def test_another_client_cannot_reuse_a_utm_source_on_the_same_course(self):
        self.make_utm()
        stolen = CourseLink(
            client=self.other_client, course=self.course, kind=CourseLink.UTM, value='news', utm_medium='email'
        )
        with self.assertRaises(ValidationError):
            stolen.full_clean()
        self.api.force_authenticate(self.staff)
        response = self.api.post('/adminpanel/course-links/', {
            'client': self.other_client.pk, 'course': COURSE_KEY, 'kind': 'utm', 'value': 'news',
            'utm_medium': 'email',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('another client', response.json()['value'][0])
        own = CourseLink(client=self.client_obj, course=self.course, kind=CourseLink.UTM, value='news',
                         utm_medium='email')
        own.full_clean()  # the same client may add more specific links

    def test_equally_specific_links_go_to_the_oldest_and_inactive_ones_fall_back(self):
        generic = self.make_utm()
        by_medium = self.make_utm(utm_medium='email')
        by_campaign = self.make_utm(utm_campaign='spring')
        self.visit(SLUG, '?utm_source=news&utm_medium=email&utm_campaign=spring')  # tie: oldest wins
        self.assertEqual(self.stat(by_medium).hits, 1)
        self.assertIsNone(self.stat(by_campaign))
        CourseLink.objects.filter(pk=by_medium.pk).update(is_active=False)
        self.visit(SLUG, '?utm_source=news&utm_medium=email&utm_campaign=spring')
        self.assertEqual(self.stat(by_campaign).hits, 1)
        CourseLink.objects.filter(pk=by_campaign.pk).update(is_active=False)
        self.visit(SLUG, '?utm_source=news&utm_medium=email')
        self.assertEqual(self.stat(generic).hits, 1)

    def test_save_with_update_fields_keeps_the_alias_slug_in_step(self):
        link = CourseLink.objects.get(pk=self.alias.pk)
        link.value = 'renamed-alias'
        link.save(update_fields=['value'])
        self.assertEqual(CourseLink.objects.get(pk=self.alias.pk).alias_slug, 'renamed-alias')

    def test_clean_without_a_course_is_a_no_op(self):
        self.assertIsNone(CourseLink(client=self.client_obj, kind=CourseLink.UTM, value='x').clean())

    def test_a_deleted_course_keeps_links_stats_and_the_report(self):
        CourseLinkDailyStat.objects.create(link=self.alias, date=today(), hits=5, unique_visitors=2)
        CourseOverview.objects.filter(pk=self.course.pk).delete()
        self.assertEqual(CourseLink.objects.filter(pk=self.alias.pk).count(), 1)
        stats = client_stats(self.client_obj, today() - timedelta(days=2), today())
        by_value = {item['value']: item for item in stats['links']}
        self.assertEqual(by_value['business-planning-acme']['hits'], 5)
        self.assertEqual(by_value['business-planning-acme']['course_name'], '')
        self.api.force_authenticate(self.staff)
        listed = self.api.get('/adminpanel/course-links/?client={}'.format(self.client_obj.pk))
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()[0]['course_name'], '')
        page = self.client.get('/course-link-report/{}/'.format(self.client_obj.report_token))
        self.assertEqual(page.status_code, 200)
        self.assertIn(COURSE_KEY, page.content.decode('utf-8'))

    def test_admin_url_column_loads_the_slug_maps_once_per_request(self):
        for index in range(5):
            CourseLink.objects.create(
                client=self.client_obj, course=self.course, kind=CourseLink.ALIAS, value='extra-alias-{}'.format(index)
            )
        link_admin = admin.site._registry[CourseLink]
        link_admin.get_queryset(self.request)
        with CaptureQueriesContext(connection) as queries:
            urls = [link_admin.url(link) for link in CourseLink.objects.all()]
        self.assertEqual(len(urls), 7)
        self.assertFalse([q for q in queries.captured_queries if 'waffle_switch' in q['sql']])

    def test_existing_links_are_read_only_in_the_admin_except_is_active(self):
        link_admin = admin.site._registry[CourseLink]
        locked = link_admin.get_readonly_fields(self.request, self.alias)
        for field in ('client', 'course', 'kind', 'value', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content'):
            self.assertIn(field, locked)
        self.assertNotIn('is_active', locked)
        self.assertNotIn('client', link_admin.get_readonly_fields(self.request, None))
        inline = admin.site._registry[CourseLinkClient].inlines[0](CourseLinkClient, admin.site)
        self.assertFalse(inline.has_change_permission(self.request))

    def test_inline_rejects_two_rows_defining_the_same_link(self):
        inline_class = admin.site._registry[CourseLinkClient].inlines[0]
        formset_class = inline_class(CourseLinkClient, admin.site).get_formset(self.request, self.client_obj)
        prefix = formset_class.get_default_prefix()

        def data(*rows):
            result = {
                prefix + '-TOTAL_FORMS': str(len(rows)), prefix + '-INITIAL_FORMS': '0',
                prefix + '-MIN_NUM_FORMS': '0', prefix + '-MAX_NUM_FORMS': '1000',
            }
            for index, row in enumerate(rows):
                for key, value in row.items():
                    result['{}-{}-{}'.format(prefix, index, key)] = value
            return result

        base = {'kind': 'utm', 'value': 'dup', 'course': COURSE_KEY, 'is_active': 'on'}
        same = formset_class(data(base, dict(base, value='DUP')), instance=self.client_obj,
                             queryset=CourseLink.objects.none())
        self.assertFalse(same.is_valid())
        self.assertIn('Two rows define the same link.', same.non_form_errors())
        alias = dict(base, kind='alias', value='same-alias')
        twice = formset_class(data(alias, dict(alias, course='course-v1:PSW+TL08+2025')), instance=self.client_obj,
                              queryset=CourseLink.objects.none())
        self.assertFalse(twice.is_valid())
        different = formset_class(data(base, dict(base, value='other')), instance=self.client_obj,
                                  queryset=CourseLink.objects.none())
        different.is_valid()
        self.assertNotIn('Two rows define the same link.', different.non_form_errors())

    def test_zero_visit_report_still_wires_copy_and_sorting(self):
        CourseLinkDailyStat.objects.all().delete()
        body = self.client.get('/course-link-report/{}/'.format(self.client_obj.report_token)).content.decode('utf-8')
        self.assertIn('No visits in this period.', body)
        self.assertNotIn('id="chart-link"', body)
        self.assertIn('class="copy"', body)
        self.assertNotIn('if(!sel) return;\nvar box', body)  # the chart-only guard no longer ends the script early
        self.assertIn("table.querySelectorAll('.copy')", body)
        self.assertIn("th.sortable", body)

    def test_inline_links_cannot_be_added_before_the_client_exists(self):
        inline = admin.site._registry[CourseLinkClient].inlines[0](CourseLinkClient, admin.site)
        self.assertFalse(inline.has_add_permission(self.request, None))  # the "add client" page
        self.assertTrue(inline.has_add_permission(self.request, self.client_obj))

    def test_changing_a_clients_organization_keeps_its_links_inside_it(self):
        acme = Organization.objects.create(name='Acme Org', short_name='acme')
        globex = Organization.objects.create(name='Globex Org', short_name='globex')
        CourseOverview.objects.filter(pk=self.course.pk).update(org='acme')
        CourseLinkClient.objects.filter(pk=self.client_obj.pk).update(organization=acme)
        self.api.force_authenticate(self.staff)
        url = '/adminpanel/course-link-clients/{}/'.format(self.client_obj.pk)
        refused = self.api.patch(url, {'organization': globex.pk}, format='json')
        self.assertEqual(refused.status_code, 400)
        self.assertIn(COURSE_KEY, refused.json()['organization'][0])
        self.assertEqual(self.api.patch(url, {'organization': None}, format='json').status_code, 200)
        self.assertEqual(self.api.patch(url, {'organization': acme.pk}, format='json').status_code, 200)
        form_class = admin.site._registry[CourseLinkClient].get_form(self.request)
        data = {'name': 'Acme', 'organization': globex.pk, 'is_report_enabled': 'on'}
        form = form_class(data, instance=self.client_obj)
        form.is_valid()
        self.assertIn('organization', form.errors)
        data['organization'] = acme.pk
        self.assertNotIn('organization', form_class(data, instance=self.client_obj).errors)

    def test_inline_checks_the_organization_changed_in_the_same_submit(self):
        CourseOverview.objects.filter(pk=self.course.pk).update(org='acme')
        globex = Organization.objects.create(name='Globex Org', short_name='globex')
        self.client_obj.organization = globex  # what the admin has in memory before saving the parent form
        inline_class = admin.site._registry[CourseLinkClient].inlines[0]
        formset_class = inline_class(CourseLinkClient, admin.site).get_formset(self.request, self.client_obj)
        prefix = formset_class.get_default_prefix()
        data = {
            prefix + '-TOTAL_FORMS': '1', prefix + '-INITIAL_FORMS': '0', prefix + '-MIN_NUM_FORMS': '0',
            prefix + '-MAX_NUM_FORMS': '1000', prefix + '-0-kind': 'utm', prefix + '-0-value': 'fresh',
            prefix + '-0-course': COURSE_KEY, prefix + '-0-is_active': 'on',
        }
        formset = formset_class(data, instance=self.client_obj, queryset=CourseLink.objects.none())
        self.assertFalse(formset.is_valid())
        self.assertTrue(any('does not belong' in error for error in formset.non_form_errors()))

    def test_organization_names_are_compared_whole_and_arbisoft_keeps_its_pakx_courses(self):
        self.assertTrue(org_matches('Acme', 'acme'))
        self.assertFalse(org_matches('acme', 'ac'))
        self.assertFalse(org_matches('aXb', 'a.b'))  # a dot in a short name is not a wildcard
        self.assertTrue(org_matches('a.b', 'a.b'))
        self.assertTrue(org_matches('PAKX', 'Arbisoft'))  # as in utils.get_org_regex
        self.assertFalse(org_matches('pakx', 'acme'))
