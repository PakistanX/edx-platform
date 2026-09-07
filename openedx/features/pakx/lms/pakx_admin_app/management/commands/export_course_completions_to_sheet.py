"""
Export learners who have completed a course (CourseProgressStats.progress >= 100)
to a Google Sheet. Intended to run daily from cron / celery-beat so the sheet is
refreshed automatically.

The sheet is *pushed to* by the platform using a Google service account, so no
edX credential ever leaves the server and no secret has to live inside the sheet
or an Apps Script. The only setup the sheet owner does is share the sheet with
the service account's email (Editor).

No Google client libraries are used (they don't support Python 3.5): the
service-account access token is minted with a signed JWT (PyJWT + cryptography,
already installed) and the Sheets REST API v4 is called with requests.

Usage:
    python manage.py lms export_course_completions_to_sheet \
        --course course-v1:Org+Num+Run \
        --spreadsheet <spreadsheet_id> \
        [--sheet Sheet1] \
        [--sa-key /edx/etc/completions-sa.json]

Service-account credentials are resolved in this order:
  1. --sa-key <path>                    (explicit JSON key file)
  2. settings.GOOGLE_SHEETS_SA_INFO     (the key JSON inlined as a dict, e.g. in lms.yml)
  3. settings.GOOGLE_SHEETS_SA_KEYFILE  (path to the JSON key file)
"""
from __future__ import absolute_import, unicode_literals

import json
import time

import jwt
import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey

from lms.djangoapps.certificates.models import CertificateStatuses, GeneratedCertificate
from openedx.features.pakx.lms.overrides.models import CourseProgressStats

GOOGLE_SHEETS_SCOPE = 'https://www.googleapis.com/auth/spreadsheets'
SHEETS_API_BASE = 'https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}'
HEADER = ['Username', 'Email', 'Progress', 'Completion Date', 'Completed Via', 'Exported At (UTC)']


class Command(BaseCommand):
    help = 'Export completed learners (progress >= 100) of a course to a Google Sheet.'

    def add_arguments(self, parser):
        parser.add_argument('--course', required=True, help='Course id, e.g. course-v1:Org+Num+Run')
        parser.add_argument('--spreadsheet', required=True, help='Google spreadsheet id (from its URL)')
        parser.add_argument('--sheet', default='Sheet1', help='Target tab/sheet name (default: Sheet1)')
        parser.add_argument(
            '--sa-key', default=None,
            help='Path to the service-account JSON key. Defaults to settings.GOOGLE_SHEETS_SA_KEYFILE.',
        )

    def handle(self, *args, **options):
        course_id = options['course']
        spreadsheet_id = options['spreadsheet']
        sheet_name = options['sheet']

        try:
            course_key = CourseKey.from_string(course_id)
        except InvalidKeyError:
            raise CommandError('Invalid course id: {}'.format(course_id))

        service_account = self._resolve_service_account(options['sa_key'])

        rows = self._completed_rows(course_key)
        self.stdout.write('Found {} completed learners for {}'.format(len(rows), course_id))

        token = self._google_access_token(service_account)
        self._write_sheet(spreadsheet_id, sheet_name, token, [HEADER] + rows)

        self.stdout.write(self.style.SUCCESS(
            'Exported {} rows to spreadsheet {} ({}).'.format(len(rows), spreadsheet_id, sheet_name)
        ))

    def _completed_rows(self, course_key):
        """
        Rows for every learner the course considers complete, by EITHER signal:
          - stored progress >= 100 (CourseProgressStats), or
          - a downloadable certificate (GeneratedCertificate).
        The two sets are unioned (a learner counts once), and a "Completed Via"
        column records which signal(s) fired.
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

        rows.sort(key=lambda row: row[0].lower())  # by username
        return rows

    @staticmethod
    def _resolve_service_account(sa_key_path):
        """
        Resolve the service-account credentials, in order of precedence:
          1. --sa-key <path>            (explicit JSON key file)
          2. settings.GOOGLE_SHEETS_SA_INFO   (inline dict in lms.yml, etc.)
          3. settings.GOOGLE_SHEETS_SA_KEYFILE (JSON key file path in settings)
        """
        if sa_key_path:
            return Command._load_service_account_file(sa_key_path)
        info = getattr(settings, 'GOOGLE_SHEETS_SA_INFO', None)
        if info:
            return Command._validate_service_account(dict(info))
        keyfile = getattr(settings, 'GOOGLE_SHEETS_SA_KEYFILE', None)
        if keyfile:
            return Command._load_service_account_file(keyfile)
        raise CommandError(
            'No service-account credentials. Set GOOGLE_SHEETS_SA_INFO (inline dict) '
            'or GOOGLE_SHEETS_SA_KEYFILE (path) in settings, or pass --sa-key.'
        )

    @staticmethod
    def _load_service_account_file(path):
        try:
            with open(path) as handle:
                data = json.load(handle)
        except (IOError, OSError, ValueError) as exc:
            raise CommandError('Could not read service-account key {}: {}'.format(path, exc))
        return Command._validate_service_account(data)

    @staticmethod
    def _validate_service_account(data):
        for field in ('client_email', 'private_key', 'token_uri'):
            if not data.get(field):
                raise CommandError('Service-account credentials missing "{}".'.format(field))
        return data

    @staticmethod
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
            raise CommandError('Google token request failed ({}): {}'.format(
                response.status_code, response.text))
        return response.json()['access_token']

    @staticmethod
    def _write_sheet(spreadsheet_id, sheet_name, token, values):
        """Clear the tab, then write header + rows (full refresh each run)."""
        base = SHEETS_API_BASE.format(spreadsheet_id=spreadsheet_id)
        headers = {'Authorization': 'Bearer {}'.format(token)}

        clear = requests.post(
            '{base}/values/{sheet}:clear'.format(base=base, sheet=sheet_name),
            headers=headers, timeout=30,
        )
        if clear.status_code != 200:
            raise CommandError('Sheets clear failed ({}): {}'.format(clear.status_code, clear.text))

        update = requests.put(
            '{base}/values/{sheet}!A1'.format(base=base, sheet=sheet_name),
            headers=headers,
            params={'valueInputOption': 'RAW'},
            json={'values': values},
            timeout=60,
        )
        if update.status_code != 200:
            raise CommandError('Sheets update failed ({}): {}'.format(update.status_code, update.text))
