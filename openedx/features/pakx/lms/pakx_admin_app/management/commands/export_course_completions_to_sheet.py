"""
Export learners who have completed a course to a Google Sheet (manual / backfill).

For the automated daily run see the ``export_course_completions`` celery task and
``settings.COURSE_COMPLETIONS_SHEET_EXPORTS``; both share the core logic in
``pakx_admin_app.completions_export``.

Usage:
    python manage.py lms export_course_completions_to_sheet \
        --course course-v1:Org+Num+Run \
        --spreadsheet <spreadsheet_id> \
        [--sheet Sheet1] \
        [--sa-key /edx/etc/completions-sa.json]

Service-account credentials resolve as: --sa-key file >
settings.GOOGLE_SHEETS_SA_INFO (inline dict) > settings.GOOGLE_SHEETS_SA_KEYFILE.
"""
from __future__ import absolute_import, unicode_literals

from django.core.management.base import BaseCommand, CommandError

from ...completions_export import CompletionsExportError, export_course_completions


class Command(BaseCommand):
    help = 'Export completed learners (progress >= 100 or a certificate) of a course to a Google Sheet.'

    def add_arguments(self, parser):
        parser.add_argument('--course', required=True, help='Course id, e.g. course-v1:Org+Num+Run')
        parser.add_argument('--spreadsheet', required=True, help='Google spreadsheet id (from its URL)')
        parser.add_argument('--sheet', default='Sheet1', help='Target tab/sheet name (default: Sheet1)')
        parser.add_argument(
            '--sa-key', default=None,
            help='Path to the service-account JSON key. Defaults to settings.GOOGLE_SHEETS_SA_KEYFILE.',
        )

    def handle(self, *args, **options):
        try:
            count = export_course_completions(
                options['course'], options['spreadsheet'],
                sheet_name=options['sheet'], sa_key_path=options['sa_key'],
            )
        except CompletionsExportError as exc:
            raise CommandError(str(exc))

        self.stdout.write(self.style.SUCCESS(
            'Exported {} rows to spreadsheet {} ({}).'.format(
                count, options['spreadsheet'], options['sheet'])
        ))
