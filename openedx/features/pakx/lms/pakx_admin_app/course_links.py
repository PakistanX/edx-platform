"""
Tracked course links: alias slugs and utm sources that lead to a course's custom landing page
(/trainings/<slug>/ or /courses/<slug>/), with raw hit and unique visitor counting.
"""
import logging
import re
import uuid
from collections import defaultdict
from datetime import datetime, timedelta

import pytz
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Sum
from django.utils import timezone
from six import text_type

from openedx.features.pakx.lms.overrides.utils import get_course_slug_mapping, get_training_slug_mapping
from openedx.features.pakx.lms.overrides.waffle import course_link_tracking_enabled

from openedx.core.djangoapps.content.course_overviews.models import CourseOverview

from .models import CourseLink, CourseLinkClient, CourseLinkDailyStat, CourseLinkVisitor

log = logging.getLogger(__name__)

VISITOR_COOKIE = 'ilmx_vid'
VISITOR_COOKIE_MAX_AGE = 365 * 24 * 60 * 60
VISITOR_ID_RE = re.compile(r'^[0-9a-f]{32}$')
ALIAS_RE = re.compile(r'^[a-z0-9][a-z0-9-]{2,99}$')
UTM_RE = re.compile(r'^[a-z0-9][a-z0-9_.-]{1,99}$')  # utm_source
UTM_EXTRA_RE = re.compile(r'^[a-z0-9][a-z0-9_.-]{0,49}$')  # medium, campaign, term, content
# Crawlers, link-preview fetchers (WhatsApp, Telegram, Slack...) and uptime/CLI clients are not visits.
BOT_UA_RE = re.compile(
    r'(?<!cu)bot|crawl|spider|slurp|preview|facebookexternalhit|whatsapp|telegram|curl|wget|python-requests|'
    r'httpclient|headless|lighthouse|uptime|monitor', re.I
)
REPORT_TZ = pytz.timezone('Asia/Karachi')
DEFAULT_REPORT_DAYS = 30
MAX_REPORT_DAYS = 366
# Visitor rows must cover a full period plus the previous one the report compares it with.
VISITOR_RETENTION_DAYS = 2 * MAX_REPORT_DAYS + 1


def today():
    """Current date in the reporting time zone (Asia/Karachi); stats are bucketed by it."""
    return timezone.now().astimezone(REPORT_TZ).date()


def slug_mappings():
    """(training, course) slug -> course key maps; each is a DB read, so load once per request."""
    return get_training_slug_mapping(), get_course_slug_mapping()


def canonical_slug_for_course(course_key, mappings=None):
    """
    Returns (url prefix, slug) of the course's canonical custom URL, or (None, None).
    """
    course_key = text_type(course_key)
    training_mapping, course_mapping = mappings or slug_mappings()
    for prefix, mapping in (('trainings', training_mapping), ('courses', course_mapping)):
        for slug in sorted(mapping):  # sorted: py3.5 dicts are unordered, keep the choice stable
            if mapping[slug] == course_key:
                return prefix, slug
    return None, None


def canonical_url(course_key, mappings=None):
    """Absolute canonical page URL of a course, or None when it has no custom URL."""
    prefix, slug = canonical_slug_for_course(course_key, mappings)
    return '{}/{}/{}/'.format(settings.LMS_ROOT_URL.rstrip('/'), prefix, slug) if slug else None


def reserved_slugs(mappings=None):
    """All canonical slugs; an alias may not reuse one."""
    training_mapping, course_mapping = mappings or slug_mappings()
    return set(training_mapping) | set(course_mapping)


def course_in_client_org(course, client_id):
    """
    True when the client has no organization, or the course belongs to it. The match is anchored (and
    case-insensitive) so that organization ``ac`` does not take ``acme``'s courses.
    """
    # utils pulls in a lot of the app, so import it here to keep this module light
    from .utils import get_org_regex  # pylint: disable=import-outside-toplevel
    short_name = CourseLinkClient.objects.filter(
        pk=client_id
    ).values_list('organization__short_name', flat=True).first()
    if not short_name:
        return True
    return CourseOverview.objects.filter(
        pk=course.pk, org__iregex='^{}$'.format(get_org_regex(short_name))
    ).exists()


def link_errors(kind, value, course, pk=None, extras=None, client_id=None):
    """
    Problems with a link definition as {field: message} (empty when valid). Shared by the admin-panel
    API and the Django admin so both enforce the same rules; ``pk`` excludes the link being edited.
    ``extras`` holds the optional utm_medium / utm_campaign / utm_term / utm_content values. A utm source
    on a course belongs to one client only (``client_id``): a more specific link of another client would
    otherwise take over part of that client's visits.
    """
    value = (value or '').strip().lower()
    extras = {field: (extras or {}).get(field, '').strip().lower() for field in CourseLink.UTM_EXTRA_FIELDS}
    mappings = slug_mappings()
    others = CourseLink.objects.exclude(pk=pk)
    if canonical_slug_for_course(course.id, mappings)[1] is None:
        return {'course': 'This course has no custom /trainings/ or /courses/ URL; add its slug mapping first.'}
    if client_id is not None and not course_in_client_org(course, client_id):
        return {'course': "This course does not belong to the client's organization."}
    if kind == CourseLink.ALIAS:
        if not ALIAS_RE.match(value):
            return {'value': 'Alias must be 3-100 characters: lowercase letters, digits and dashes.'}
        if value in reserved_slugs(mappings):
            return {'value': 'This is already a canonical course slug.'}
        if others.filter(kind=CourseLink.ALIAS, value=value).exists():
            return {'value': 'This alias is already in use.'}
        for field, extra in sorted(extras.items()):
            if extra:
                return {field: 'Only UTM links can have UTM parameters.'}
    else:
        if not UTM_RE.match(value):
            return {'value': 'UTM source must be 2-100 characters: lowercase letters, digits, dash, dot, underscore.'}
        for field, extra in sorted(extras.items()):
            if extra and not UTM_EXTRA_RE.match(extra):
                return {field: '{} must be 1-50 characters: lowercase letters, digits, dash, dot, underscore.'.format(
                    field
                )}
        if others.filter(kind=CourseLink.UTM, value=value, course=course, **extras).exists():
            return {'value': 'This combination of UTM parameters is already registered for the course.'}
        if client_id is not None and others.filter(
                kind=CourseLink.UTM, value=value, course=course
        ).exclude(client_id=client_id).exists():
            return {'value': 'This utm source is already used on this course by another client.'}
    return {}


def link_url(link, mappings=None):
    """Absolute URL to hand to the client for the given link."""
    prefix, slug = canonical_slug_for_course(link.course_id, mappings)
    base = settings.LMS_ROOT_URL.rstrip('/')
    if link.kind == CourseLink.ALIAS:
        return '{}/{}/{}/'.format(base, prefix or 'trainings', link.value)
    query = ['utm_source={}'.format(link.value)] + [
        '{}={}'.format(field, getattr(link, field)) for field in CourseLink.UTM_EXTRA_FIELDS if getattr(link, field)
    ]
    return '{}/{}/{}/?{}'.format(base, prefix or 'trainings', slug or '', '&'.join(query))


def resolve_alias(slug):
    """Active alias link for the slug, or None."""
    return CourseLink.objects.filter(
        kind=CourseLink.ALIAS, value=(slug or '').lower(), is_active=True
    ).select_related('course').order_by().first()  # order_by(): skip the default ordering's join and sort


def _clean_utm(value, pattern):
    value = (value or '').strip().lower()
    return value if pattern.match(value) else ''


def match_utm_link(course_key, params):
    """
    Active utm link of this course that best matches the request's utm_* parameters (a dict-like such
    as request.GET), or None. A link matches when its utm_source is the request's and every other
    parameter it fills in equals the request's; blank ones match anything. The link filling in the most
    parameters wins, so ``?utm_source=a&utm_medium=email`` counts for a source+medium link rather than
    for a plain source one. Unregistered values are ignored so arbitrary query strings create no rows.
    """
    source = _clean_utm(params.get('utm_source'), UTM_RE)
    if not source:
        return None
    request_extras = {field: _clean_utm(params.get(field), UTM_EXTRA_RE) for field in CourseLink.UTM_EXTRA_FIELDS}
    best, best_score = None, -1  # ties go to the oldest link: links are visited in pk order and only a strict gain wins
    for link in CourseLink.objects.filter(
            kind=CourseLink.UTM, value=source, course_id=course_key, is_active=True
    ).order_by('pk'):
        filled = [field for field in CourseLink.UTM_EXTRA_FIELDS if getattr(link, field)]
        if all(getattr(link, field) == request_extras[field] for field in filled) and len(filled) > best_score:
            best, best_score = link, len(filled)
    return best


def tracked_utm_link(request, course_key):
    """The registered utm link of this request, looked up only when the visit would be counted anyway."""
    if 'utm_source' not in request.GET or not course_link_tracking_enabled() or not is_countable_request(request):
        return None
    try:
        return match_utm_link(course_key, request.GET)
    except Exception:  # pylint: disable=broad-except
        log.exception('Could not look up the utm link for course %s', course_key)  # counting must not break the page
        return None


def is_countable_request(request):
    """Plain GET page loads by real browsers; not HEAD, prefetch/preview requests or known bots."""
    if request.method != 'GET':
        return False
    purposes = (
        request.META.get('HTTP_PURPOSE', ''), request.META.get('HTTP_X_PURPOSE', ''),
        request.META.get('HTTP_SEC_PURPOSE', ''), request.META.get('HTTP_SEC_FETCH_PURPOSE', ''),
    )
    if any(p.lower() in ('prefetch', 'preview') or p.lower().startswith('prefetch') for p in purposes):
        return False
    user_agent = request.META.get('HTTP_USER_AGENT', '')
    return bool(user_agent) and not BOT_UA_RE.search(user_agent)


def _first_visit_today(link, visitor_id, day):
    """Stores the visitor row; False when this visitor was already counted for the link today."""
    try:
        with transaction.atomic():
            CourseLinkVisitor.objects.create(link=link, visitor_id=visitor_id, date=day)
    except IntegrityError:
        return False
    return True


def _count(link, visitor_id, day):
    """
    Add one hit (and one unique visitor on a first visit) to the link's stats for ``day``. Updates in
    place and creates the day's row only when none exists, so concurrent requests cannot lose a hit.
    """
    with transaction.atomic():
        fields = {'hits': F('hits') + 1}
        is_new_today = _first_visit_today(link, visitor_id, day)
        if is_new_today:
            fields['unique_visitors'] = F('unique_visitors') + 1
        if CourseLinkDailyStat.objects.filter(link=link, date=day).update(**fields):
            return
        try:
            with transaction.atomic():
                CourseLinkDailyStat.objects.create(link=link, date=day, hits=1, unique_visitors=int(is_new_today))
        except IntegrityError:  # another request created the day's row first
            CourseLinkDailyStat.objects.filter(link=link, date=day).update(**fields)


def record_visit(request, response, link):
    """
    Count one visit to `link`: +1 hit, and +1 unique visitor the first time this browser (random
    first-party cookie) visits the link that day. Never raises: counting must not break the page.
    """
    if link is None or not course_link_tracking_enabled() or not is_countable_request(request):
        return
    visitor_id = request.COOKIES.get(VISITOR_COOKIE, '')
    if not VISITOR_ID_RE.match(visitor_id):
        visitor_id = uuid.uuid4().hex
    try:
        _count(link, visitor_id, today())
    except Exception:  # pylint: disable=broad-except
        log.exception('Could not record visit for course link %s', link.pk)
        return
    response.set_cookie(
        VISITOR_COOKIE, visitor_id, max_age=VISITOR_COOKIE_MAX_AGE, httponly=True, samesite='Lax',
        secure=request.is_secure(),
    )


def parse_report_range(date_from=None, date_to=None, days=None):
    """
    (start, end) dates for a report, inclusive. Defaults to the last DEFAULT_REPORT_DAYS days;
    ``days`` counts back from today. Raises ValueError on bad input or a range over MAX_REPORT_DAYS.
    """
    end = today()
    if days not in (None, ''):
        days = int(days)
        if not 1 <= days <= MAX_REPORT_DAYS:
            raise ValueError('days must be between 1 and {}'.format(MAX_REPORT_DAYS))
        return end - timedelta(days=days - 1), end
    if date_from or date_to:
        start = _parse_date(date_from) if date_from else end - timedelta(days=DEFAULT_REPORT_DAYS - 1)
        end = min(_parse_date(date_to), end) if date_to else end  # nothing has been counted for future days
    else:
        start = end - timedelta(days=DEFAULT_REPORT_DAYS - 1)
    if start > end:
        raise ValueError('from must not be after to')
    if (end - start).days + 1 > MAX_REPORT_DAYS:
        raise ValueError('range must not exceed {} days'.format(MAX_REPORT_DAYS))
    if start < _oldest_detailed_day():
        raise ValueError('range must start within the last {} days'.format(VISITOR_RETENTION_DAYS))
    return start, end


def _oldest_detailed_day():
    """Earliest day whose per-visitor rows are still kept (see prune_course_link_visitors)."""
    return today() - timedelta(days=VISITOR_RETENTION_DAYS)


def _parse_date(value):
    return datetime.strptime(value, '%Y-%m-%d').date()


def _period_totals(link_ids, start, end):
    """(hits, distinct visitors) of the given links over [start, end]."""
    hits = CourseLinkDailyStat.objects.filter(
        link_id__in=link_ids, date__range=(start, end)
    ).aggregate(n=Sum('hits'))['n'] or 0
    visitors = CourseLinkVisitor.objects.filter(
        link_id__in=link_ids, date__range=(start, end)
    ).aggregate(n=Count('visitor_id', distinct=True))['n']
    return hits, visitors


def client_stats(client, start, end, with_previous=True):
    """
    Stats of a client's links for [start, end]: totals, a per-day series and one entry per link.
    ``unique_visitors`` of a link (and of the client) is the number of distinct visitors in the
    period; per-day entries are that day's unique visitors, so they do not sum to the total.
    ``previous`` holds the totals of the period of the same length just before it (None when not asked
    for, or when that period reaches back beyond the kept visitor rows).
    """
    links = list(client.links.all())
    course_names = dict(CourseOverview.objects.filter(
        id__in=[link.course_id for link in links]
    ).values_list('id', 'display_name'))
    link_ids = [link.pk for link in links]
    day_rows = CourseLinkDailyStat.objects.filter(link_id__in=link_ids, date__range=(start, end))
    visitor_rows = CourseLinkVisitor.objects.filter(link_id__in=link_ids, date__range=(start, end))

    per_link = {link.pk: {'hits': 0, 'series': {}} for link in links}
    hits_by_day = defaultdict(int)
    for row in day_rows:
        per_link[row.link_id]['hits'] += row.hits
        per_link[row.link_id]['series'][row.date] = (row.hits, row.unique_visitors)
        hits_by_day[row.date] += row.hits

    uniques_by_link = dict(
        visitor_rows.values_list('link_id').annotate(n=Count('visitor_id', distinct=True))
    )
    uniques_by_day = dict(visitor_rows.values_list('date').annotate(n=Count('visitor_id', distinct=True)))
    total_uniques = visitor_rows.aggregate(n=Count('visitor_id', distinct=True))['n']

    mappings = slug_mappings()
    span = (end - start).days + 1
    previous_end = start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=span - 1)
    previous = None
    if with_previous and previous_start >= _oldest_detailed_day():
        previous_hits, previous_visitors = _period_totals(link_ids, previous_start, previous_end)
        previous = {
            'from': text_type(previous_start), 'to': text_type(previous_end),
            'hits': previous_hits, 'unique_visitors': previous_visitors,
        }
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    link_payload = []
    for link in links:
        data = per_link[link.pk]
        link_payload.append({
            'id': link.pk,
            'kind': link.kind,
            'value': link.value,
            'label': link.label,
            'utm_medium': link.utm_medium,
            'utm_campaign': link.utm_campaign,
            'utm_term': link.utm_term,
            'utm_content': link.utm_content,
            'is_active': link.is_active,
            'url': link_url(link, mappings),
            'course_id': text_type(link.course_id),
            'course_name': course_names.get(link.course_id, ''),  # '' once the course itself is gone
            'hits': data['hits'],
            'unique_visitors': uniques_by_link.get(link.pk, 0),
            'series': [
                {'date': text_type(day), 'hits': data['series'].get(day, (0, 0))[0],
                 'unique_visitors': data['series'].get(day, (0, 0))[1]}
                for day in days
            ],
        })
    return {
        'client': {'id': client.pk, 'name': client.name},
        'from': text_type(start),
        'to': text_type(end),
        'timezone': 'Asia/Karachi',
        'generated_at': timezone.now().astimezone(REPORT_TZ).isoformat(),
        'previous': previous,
        'hits': sum(item['hits'] for item in link_payload),
        'unique_visitors': total_uniques,
        'series': [
            {'date': text_type(day), 'hits': hits_by_day[day],
             'unique_visitors': uniques_by_day.get(day, 0)}
            for day in days
        ],
        'links': link_payload,
    }
