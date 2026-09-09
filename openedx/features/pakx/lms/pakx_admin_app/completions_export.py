"""
Push learners who have completed a course to a Google Sheet.

Completion is EITHER signal: stored progress >= 100 (CourseProgressStats) or a
downloadable certificate (GeneratedCertificate). The platform pushes to the sheet
with a Google service account, so no edX credential leaves the server and no
secret lives in the sheet / an Apps Script -- the sheet owner only shares the
sheet with the service account's email.

No Google client libraries are used (they don't support Python 3.5): the
service-account access token is minted from a signed RS256 JWT (PyJWT +
cryptography, already installed) and the Sheets REST API v4 is called with
requests.

Used by both the ``export_course_completions_to_sheet`` management command
(manual / backfill) and the ``export_course_completions`` celery task (daily,
driven by ``settings.COURSE_COMPLETIONS_SHEET_EXPORTS``).
"""
from __future__ import absolute_import, unicode_literals

import json
import time
from logging import getLogger

import jwt
import requests
from django.conf import settings
from django.utils import timezone
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey

from lms.djangoapps.certificates.models import CertificateStatuses, GeneratedCertificate
from openedx.features.pakx.lms.overrides.models import CourseProgressStats

log = getLogger(__name__)

GOOGLE_SHEETS_SCOPE = 'https://www.googleapis.com/auth/spreadsheets'
SHEETS_API_BASE = 'https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}'
HEADER = ['Username', 'Email', 'Progress', 'Completion Date', 'Completed Via', 'Exported At (UTC)']


class CompletionsExportError(Exception):
    """Raised for any expected failure while exporting completions to a sheet."""


def export_course_completions(course_id, spreadsheet_id, sheet_name='Sheet1', sa_key_path=None):
    """
    Export completed learners of ``course_id`` to the given Google spreadsheet
    (full refresh: clears the tab, writes header + rows). Returns the learner row
    count. Raises CompletionsExportError on any expected failure.
    """
    try:
        course_key = CourseKey.from_string(course_id)
    except InvalidKeyError:
        raise CompletionsExportError('Invalid course id: {}'.format(course_id))

    service_account = resolve_service_account(sa_key_path)
    rows = completed_rows(course_key)
    token = _google_access_token(service_account)
    _write_sheet(spreadsheet_id, sheet_name, token, [HEADER] + rows)
    log.info('Exported %s completed learners for %s to spreadsheet %s (%s)',
             len(rows), course_id, spreadsheet_id, sheet_name)
    return len(rows)


def export_all_configured():
    """
    Run the export for every entry in ``settings.COURSE_COMPLETIONS_SHEET_EXPORTS``
    (a list of dicts with keys ``course``, ``spreadsheet`` and optional ``sheet``).
    Each course is exported independently; a failure is logged and skipped so it
    does not block the others. Returns ``(succeeded, failed)`` counts.
    """
    exports = getattr(settings, 'COURSE_COMPLETIONS_SHEET_EXPORTS', None) or []
    succeeded = failed = 0
    for entry in exports:
        course = entry.get('course')
        spreadsheet = entry.get('spreadsheet')
        if not course or not spreadsheet:
            log.error('Skipping malformed COURSE_COMPLETIONS_SHEET_EXPORTS entry: %r', entry)
            failed += 1
            continue
        try:
            count = export_course_completions(course, spreadsheet, sheet_name=entry.get('sheet', 'Sheet1'))
            log.info('Completions export: %s rows for %s -> %s', count, course, spreadsheet)
            succeeded += 1
        except CompletionsExportError as exc:
            log.error('Completions export failed for %s: %s', course, exc)
            failed += 1
        except Exception:  # pylint: disable=broad-except
            log.exception('Unexpected error exporting completions for %s', course)
            failed += 1
    return succeeded, failed


def completed_rows(course_key):
    """
    Rows for every learner the course considers complete, by EITHER signal:
      - stored progress >= 100 (CourseProgressStats), or
      - a downloadable certificate (GeneratedCertificate).
    Unioned (a learner counts once); a "Completed Via" column records which fired.
    """
    exported_at = timezone.now().strftime('%Y-%m-%d %H:%M:%S')

    stats_by_user = {
        stat.enrollment.user_id: stat
        for stat in CourseProgressStats.objects
        .filter(enrollment__course_id=course_key, progress__gte=100)
        .select_related('enrollment__user')
    }
    certs_by_user = {
        cert.user_id: cert
        for cert in GeneratedCertificate.objects
        .filter(course_id=course_key, status=CertificateStatuses.downloadable)
        .select_related('user')
    }

    rows = []
    for user_id in set(stats_by_user) | set(certs_by_user):
        stat = stats_by_user.get(user_id)
        cert = certs_by_user.get(user_id)
        user = stat.enrollment.user if stat is not None else cert.user

        progress = stat.progress if stat is not None else ''
        # Prefer the stored completion date; fall back to the certificate's.
        if stat is not None and stat.completion_date:
            completion = stat.completion_date.strftime('%Y-%m-%d')
        elif cert is not None and cert.modified_date:
            completion = cert.modified_date.strftime('%Y-%m-%d')
        else:
            completion = ''
        via = '+'.join(
            ([u'progress'] if stat is not None else []) +
            ([u'certificate'] if cert is not None else [])
        )
        rows.append([user.username, user.email, progress, completion, via, exported_at])

    # Newest completions first; ties broken by username (stable sort, so sort by
    # username first, then by completion date descending). row[3] = completion
    # date 'YYYY-MM-DD' (sorts chronologically); undated rows fall to the bottom.
    rows.sort(key=lambda row: row[0].lower())
    rows.sort(key=lambda row: row[3], reverse=True)
    return rows


def resolve_service_account(sa_key_path=None):
    """
    Resolve the service-account credentials, in order of precedence:
      1. sa_key_path                       (explicit JSON key file)
      2. settings.GOOGLE_SHEETS_SA_INFO    (inline dict in lms.yml, etc.)
      3. settings.GOOGLE_SHEETS_SA_KEYFILE (JSON key file path in settings)
    """
    if sa_key_path:
        return _load_service_account_file(sa_key_path)
    info = getattr(settings, 'GOOGLE_SHEETS_SA_INFO', None)
    if info:
        return _validate_service_account(dict(info))
    keyfile = getattr(settings, 'GOOGLE_SHEETS_SA_KEYFILE', None)
    if keyfile:
        return _load_service_account_file(keyfile)
    raise CompletionsExportError(
        'No service-account credentials. Set GOOGLE_SHEETS_SA_INFO (inline dict) '
        'or GOOGLE_SHEETS_SA_KEYFILE (path) in settings, or pass a key path.'
    )


def _load_service_account_file(path):
    try:
        with open(path) as handle:
            data = json.load(handle)
    except (IOError, OSError, ValueError) as exc:
        raise CompletionsExportError('Could not read service-account key {}: {}'.format(path, exc))
    return _validate_service_account(data)


def _validate_service_account(data):
    for field in ('client_email', 'private_key', 'token_uri'):
        if not data.get(field):
            raise CompletionsExportError('Service-account credentials missing "{}".'.format(field))
    return data


def _google_access_token(service_account):
    """Mint a Google OAuth2 access token via the service-account JWT-bearer flow."""
    now = int(time.time())
    payload = {
        'iss': service_account['client_email'],
        'scope': GOOGLE_SHEETS_SCOPE,
        'aud': service_account['token_uri'],
        'iat': now,
        'exp': now + 3600,
    }
    assertion = jwt.encode(payload, service_account['private_key'], algorithm='RS256')
    if isinstance(assertion, bytes):  # PyJWT 1.x returns bytes
        assertion = assertion.decode('ascii')
    response = requests.post(
        service_account['token_uri'],
        data={
            'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
            'assertion': assertion,
        },
        timeout=30,
    )
    if response.status_code != 200:
        raise CompletionsExportError('Google token request failed ({}): {}'.format(
            response.status_code, response.text))
    return response.json()['access_token']


def _write_sheet(spreadsheet_id, sheet_name, token, values):
    """Clear the tab, then write header + rows (full refresh each run)."""
    base = SHEETS_API_BASE.format(spreadsheet_id=spreadsheet_id)
    headers = {'Authorization': 'Bearer {}'.format(token)}

    clear = requests.post(
        '{base}/values/{sheet}:clear'.format(base=base, sheet=sheet_name),
        headers=headers, timeout=30,
    )
    if clear.status_code != 200:
        raise CompletionsExportError('Sheets clear failed ({}): {}'.format(clear.status_code, clear.text))

    update = requests.put(
        '{base}/values/{sheet}!A1'.format(base=base, sheet=sheet_name),
        headers=headers,
        params={'valueInputOption': 'RAW'},
        json={'values': values},
        timeout=60,
    )
    if update.status_code != 200:
        raise CompletionsExportError('Sheets update failed ({}): {}'.format(update.status_code, update.text))
