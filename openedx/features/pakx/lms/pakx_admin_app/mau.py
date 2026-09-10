"""
Monthly Active Users (MAU) report generation.

Active users = users whose `last_login` falls within the reported month, scoped to
an organization (their profile org OR enrollment in a course of that org). Django
staff/superusers and course staff (CourseAccessRole holders, plus a configurable
per-org username list) are excluded from the learner count but appended to the CSV
as a separate section. Reports are written to storage (S3 in production) and their
metadata is recorded in MAUReport for listing.
"""
import csv
import datetime
import io
from logging import getLogger

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db.models import Q
from django.utils import timezone

from organizations.models import Organization
from student.models import CourseAccessRole

from .constants import (
    MAU_REPORT_CSV_HEADERS,
    MAU_REPORTS_OVERALL_FOLDER,
    MAU_REPORTS_S3_FOLDER,
    MAU_USER_TYPE_ADMIN_STAFF,
    MAU_USER_TYPE_COURSE_STAFF,
    MAU_USER_TYPE_LEARNER,
)
from .models import MAUReport, MAUReportExcludedUser
from .utils import get_org_regex, get_selected_org, is_unrestricted_admin

log = getLogger(__name__)


def get_previous_month(reference=None):
    """Return (year, month) for the calendar month before `reference` (default: now)."""
    reference = reference or timezone.now()
    first_of_this_month = reference.replace(day=1)
    last_month_end = first_of_this_month - datetime.timedelta(days=1)
    return last_month_end.year, last_month_end.month


def get_month_bounds(year, month):
    """Return timezone-aware [start, end) datetimes for the given month in the server TZ."""
    start_naive = datetime.datetime(year, month, 1)
    if month == 12:
        end_naive = datetime.datetime(year + 1, 1, 1)
    else:
        end_naive = datetime.datetime(year, month + 1, 1)
    tz = timezone.get_current_timezone()
    return timezone.make_aware(start_naive, tz), timezone.make_aware(end_naive, tz)


def _org_scope_q(org_regex):
    """Users belonging to the org: their profile org OR an enrollment in the org's courses."""
    if org_regex is None:
        return Q()
    return (
        Q(profile__organization__short_name__iregex=org_regex) |
        Q(courseenrollment__course__org__iregex=org_regex)
    )


def get_course_staff_usernames(org_regex):
    """Usernames of course staff (any CourseAccessRole) in the org; all orgs when org_regex is None."""
    qs = CourseAccessRole.objects.all()
    if org_regex is not None:
        qs = qs.filter(org__iregex=org_regex)
    return set(qs.values_list('user__username', flat=True))


def get_extra_excluded_usernames(org_short_name):
    """Configured extra exclusions: the org's own list plus globally-excluded (blank org) usernames."""
    org_filter = Q(organization_short_name='')
    if org_short_name:
        org_filter |= Q(organization_short_name__iexact=org_short_name)
    return set(MAUReportExcludedUser.objects.filter(org_filter).values_list('username', flat=True))


def _row(user_type, user):
    return [
        user_type,
        user.username,
        user.email,
        user.date_joined.isoformat() if user.date_joined else '',
        user.last_login.isoformat() if user.last_login else '',
    ]


def build_mau_rows(org_regex, org_short_name, start, end):
    """
    Split active users into learner rows and appended staff rows (course staff +
    Django admin staff). Learners first, ordered by most recent login (matches the
    manual report query).
    """
    active_users = User.objects.filter(
        _org_scope_q(org_regex),
        last_login__gte=start,
        last_login__lt=end,
    ).select_related('profile').distinct().order_by('-last_login')

    excluded_course_staff = get_course_staff_usernames(org_regex) | get_extra_excluded_usernames(org_short_name)

    learner_rows, staff_rows = [], []
    for user in active_users:
        if user.is_staff or user.is_superuser:
            staff_rows.append(_row(MAU_USER_TYPE_ADMIN_STAFF, user))
        elif user.username in excluded_course_staff:
            staff_rows.append(_row(MAU_USER_TYPE_COURSE_STAFF, user))
        else:
            learner_rows.append(_row(MAU_USER_TYPE_LEARNER, user))
    return learner_rows, staff_rows


def render_csv(learner_rows, staff_rows):
    """Render learner rows followed by the appended staff rows as UTF-8 CSV bytes."""
    buff = io.StringIO()
    writer = csv.writer(buff)
    writer.writerow(MAU_REPORT_CSV_HEADERS)
    for row in learner_rows:
        writer.writerow(row)
    for row in staff_rows:
        writer.writerow(row)
    return buff.getvalue().encode('utf-8')


def _report_path(is_overall, org_short_name, year, month):
    folder = MAU_REPORTS_OVERALL_FOLDER if is_overall else org_short_name
    return '{}/{}/{:04d}-{:02d}.csv'.format(MAU_REPORTS_S3_FOLDER, folder, year, month)


def _save_report_file(path, content_bytes):
    """Overwrite any existing file at `path` (so re-runs / backfills are idempotent)."""
    if default_storage.exists(path):
        default_storage.delete(path)
    default_storage.save(path, ContentFile(content_bytes))


def generate_mau_report(year, month, organization=None):
    """
    Generate one MAU report for `organization` (an Organization instance), or the
    overall all-orgs report when organization is None. Writes the CSV to storage
    and upserts the MAUReport metadata row. Returns the MAUReport.
    """
    is_overall = organization is None
    org_short_name = '' if is_overall else (organization.short_name or '')
    org_name = '' if is_overall else (organization.name or organization.short_name or '')
    org_regex = None if is_overall else get_org_regex(org_short_name)

    start, end = get_month_bounds(year, month)
    learner_rows, staff_rows = build_mau_rows(org_regex, org_short_name, start, end)

    path = _report_path(is_overall, org_short_name, year, month)
    _save_report_file(path, render_csv(learner_rows, staff_rows))

    report, _created = MAUReport.objects.update_or_create(
        organization_short_name=org_short_name,
        is_overall=is_overall,
        month=datetime.date(year, month, 1),
        defaults={
            'organization_name': org_name,
            'active_user_count': len(learner_rows),
            'excluded_staff_count': len(staff_rows),
            'file_path': path,
        },
    )
    log.info(
        'Generated MAU report %s: %s active learners, %s excluded staff',
        report, report.active_user_count, report.excluded_staff_count,
    )
    return report


def get_scoped_mau_reports(request):
    """
    MAUReport rows visible to the requester, mirroring the dashboard org selector:

    - unrestricted admin + selected org -> that org's monthly reports
    - unrestricted admin + no org        -> the overall (all-orgs) reports
    - course staff / everyone else       -> their own org's reports only (never overall)
    """
    user = request.user
    if is_unrestricted_admin(user):
        selected_org = get_selected_org(request)
        if selected_org:
            return MAUReport.objects.filter(is_overall=False, organization_short_name__iexact=selected_org)
        return MAUReport.objects.filter(is_overall=True)

    org_short_name = getattr(getattr(user, 'profile', None), 'organization', None)
    org_short_name = getattr(org_short_name, 'short_name', '') or ''
    if not org_short_name:
        return MAUReport.objects.none()
    return MAUReport.objects.filter(is_overall=False, organization_short_name__iexact=org_short_name)


def generate_all_mau_reports(year=None, month=None):
    """
    Generate reports for every active organization plus the overall report for the
    given month, defaulting to the previous calendar month.
    """
    if year is None or month is None:
        year, month = get_previous_month()

    reports = []
    for organization in Organization.objects.filter(active=True):
        reports.append(generate_mau_report(year, month, organization=organization))
    reports.append(generate_mau_report(year, month, organization=None))
    return reports
