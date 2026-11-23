"""Helpers for the verified certificate upgrade email campaign"""

import csv
from collections import OrderedDict, namedtuple
from logging import getLogger

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ObjectDoesNotExist
from django.urls import reverse
from django.utils import timezone
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from six import text_type

from bulk_email.models import Optout
from course_modes.models import CourseMode
from lms.djangoapps.verify_student.models import VerificationDeadline
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.features.pakx.lms.overrides.models import CourseProgressStats
from student.models import CourseEnrollment

log = getLogger(__name__)

UTM_PARAMS = 'utm_source=email&utm_medium=marketing&utm_campaign=verified_upgrade'
DEFAULT_HERO_IMAGE_URL = 'https://ilmx.org/asset-v1:arbisoft+FILES1+2025_26+type@asset+block@verified-upgrade-hero.png'

InputRow = namedtuple('InputRow', ['line', 'user_id', 'email', 'course_id'])
RowResult = namedtuple('RowResult', ['row', 'user', 'course_key', 'course_title', 'progress', 'reason'])
LearnerEmail = namedtuple('LearnerEmail', ['user', 'courses', 'more_count'])


def read_input_rows(csv_file):
    """
    Read the campaign CSV into a list of InputRow.

    The file needs a course_id column and a user_id or email column. A leading byte order mark on the first header
    and whitespace around every value are ignored. An empty user_id or email cell becomes None.
    A user_id that is not a number raises a ValueError naming the line.
    """
    reader = csv.DictReader(csv_file)
    fieldnames = [name.lstrip(u'\ufeff') for name in reader.fieldnames or []]
    reader.fieldnames = fieldnames
    if 'course_id' not in fieldnames or not ({'user_id', 'email'} & set(fieldnames)):
        raise ValueError('CSV needs a course_id column and a user_id or email column')

    rows = []
    for record in reader:
        user_id = (record.get('user_id') or '').strip()
        try:
            user_id_number = int(user_id) if user_id else None
        except ValueError:
            raise ValueError('Line {line}: user_id "{value}" is not a number'.format(
                line=reader.line_num, value=user_id
            ))
        email = (record.get('email') or '').strip()
        rows.append(InputRow(
            line=reader.line_num,
            user_id=user_id_number,
            email=email or None,
            course_id=(record.get('course_id') or '').strip(),
        ))
    return rows


def cta_text(progress):
    """Return the email button text for a course progress percentage"""
    if progress >= 100:
        return 'Claim my certificate'
    if progress >= 50:
        return 'Finish strong & get certified'
    if progress > 0:
        return 'Keep going & get certified'
    return 'Start my certificate journey'


def build_upgrade_url(course_key):
    """Return the absolute upgrade link for a course, tagged with the campaign UTM parameters"""
    path = reverse('verify_student_upgrade_and_verify', kwargs={'course_id': text_type(course_key)})
    return '{root}{path}?{utm}'.format(root=settings.LMS_ROOT_URL, path=path, utm=UTM_PARAMS)


def _find_user(row):
    """Return the user named by the row, looking up user_id first and the email, ignoring case, otherwise"""
    if row.user_id is not None:
        return User.objects.filter(id=row.user_id).first()
    if row.email:
        return User.objects.filter(email__iexact=row.email).order_by('id').first()
    return None


def _parse_course_key(course_id):
    """Return the CourseKey for a course id string, or None if it is not a valid course id"""
    try:
        return CourseKey.from_string(course_id)
    except InvalidKeyError:
        return None


def _load_progress(course_keys):
    """Return a dict of (user id, course key) to course progress for the enrollments in the given courses"""
    stats = CourseProgressStats.objects.filter(enrollment__course_id__in=course_keys).select_related('enrollment')
    return {(stat.enrollment.user_id, stat.enrollment.course_id): stat.progress for stat in stats}


def _check_row(row, min_progress, progress_by_enrollment, seen):
    """Run the eligibility checks on one row, stopping at the first failure. An empty reason means eligible."""
    user = course_key = course_title = progress = None

    def result(reason):
        return RowResult(row, user, course_key, course_title, progress, reason)

    course_key = _parse_course_key(row.course_id)
    if course_key is None:
        return result('invalid course id')

    user = _find_user(row)
    if user is None:
        return result('user not found')
    if (user.id, course_key) in seen:
        return result('duplicate row')
    seen.add((user.id, course_key))

    overview = CourseOverview.objects.filter(id=course_key).first()
    if overview is None:
        return result('course not found')
    course_title = overview.display_name or ''

    enrollment = CourseEnrollment.objects.filter(user=user, course_id=course_key).first()
    if enrollment is None:
        return result('not enrolled')
    if not enrollment.is_active:
        return result('enrollment inactive')
    if enrollment.mode not in CourseMode.UPSELL_TO_VERIFIED_MODES:
        return result('already paid')
    if not user.is_active:
        return result('account inactive')

    verified_mode = CourseMode.verified_mode_for_course(course_key)
    # The upgrade page only sells a mode with a SKU and a price above zero
    if verified_mode is None or not verified_mode.sku or not verified_mode.min_price > 0:
        return result('no verified seat for sale')
    deadline = VerificationDeadline.deadline_for_course(course_key)
    if deadline is not None and deadline < timezone.now():
        return result('verification deadline passed')

    if Optout.objects.filter(user=user, course_id=course_key).exists():
        return result('opted out')

    progress = progress_by_enrollment.get((user.id, course_key), 0)
    if progress < min_progress:
        return result('progress below {}'.format(min_progress))
    return result('')


def evaluate_rows(rows, min_progress=100):
    """
    Check every input row and return a list of RowResult in the same order as the rows.

    A row is eligible when its reason is empty. Otherwise the reason says why the learner is skipped for that course.
    A missing course progress record counts as 0 progress.
    """
    parsed_keys = (_parse_course_key(row.course_id) for row in rows)
    progress_by_enrollment = _load_progress({key for key in parsed_keys if key is not None})

    results = []
    seen = set()
    seen_rows = set()
    for row in rows:
        row_identity = (row.user_id, row.email.lower() if row.email else None, row.course_id)
        if row_identity in seen_rows:
            results.append(RowResult(row, None, None, None, None, 'duplicate row'))
            continue
        seen_rows.add(row_identity)
        results.append(_check_row(row, min_progress, progress_by_enrollment, seen))
    return results


def group_into_emails(results, max_courses=3):
    """
    Group the eligible results into one LearnerEmail per learner, in order of each learner's first row.

    Each email lists the learner's eligible courses with the highest progress first, up to max_courses.
    more_count is the number of eligible courses that did not fit.
    """
    eligible_by_user = OrderedDict()
    for result in results:
        if not result.reason:
            eligible_by_user.setdefault(result.user.id, []).append(result)

    emails = []
    for eligible in eligible_by_user.values():
        ranked = sorted(eligible, key=lambda result: result.progress, reverse=True)
        emails.append(LearnerEmail(
            user=ranked[0].user, courses=ranked[:max_courses], more_count=max(len(ranked) - max_courses, 0)
        ))
    return emails


def _first_name(user):
    """Return the first word of the learner's full name, or the username when there is no name"""
    try:
        name = user.profile.name
    except ObjectDoesNotExist:
        name = ''
    words = (name or '').split()
    return words[0] if words else user.username


def build_email_context(learner_email, hero_image_url=DEFAULT_HERO_IMAGE_URL):
    """
    Return the JSON-serialisable template context for one learner's email.

    The first course card is the primary one. course_ids lists the courses on the cards, so the ACE course opt-out
    policy can check them.
    """
    courses = []
    for index, result in enumerate(learner_email.courses):
        courses.append({
            'title': result.course_title,
            'progress': int(result.progress),
            'completed': result.progress >= 100,
            'url': build_upgrade_url(result.course_key),
            'button_text': cta_text(result.progress),
            'primary': index == 0,
        })
    return {
        'first_name': _first_name(learner_email.user),
        'courses': courses,
        'more_count': learner_email.more_count,
        'dashboard_url': '{root}{path}'.format(root=settings.LMS_ROOT_URL, path=reverse('dashboard')),
        'hero_image_url': hero_image_url,
        'course_ids': [text_type(result.course_key) for result in learner_email.courses],
    }


def build_payload(learner_email, hero_image_url, to_email=None):
    """Return the JSON-serialisable payload for the sending task, addressed to to_email or else the learner"""
    return {
        'user_id': learner_email.user.id,
        'to_email': to_email if to_email is not None else learner_email.user.email,
        'context': build_email_context(learner_email, hero_image_url),
    }
