"""
Export completed learners to Google Sheets for every course configured in
``settings.COURSE_COMPLETIONS_SHEET_EXPORTS``. Intended for a daily cron run.

    ./manage.py lms export_all_course_completions

Each course is exported independently; a failing course is logged and skipped so
it does not block the others. Exits non-zero if any course failed, so cron/alerts
can notice. For a single ad-hoc course use ``export_course_completions_to_sheet``.
"""
from __future__ import absolute_import, unicode_literals

from django.core.management.base import BaseCommand

from ...completions_export import export_all_configured


class Command(BaseCommand):
    help = 'Export completed learners to Google Sheets for every configured course (cron entry point).'

    def handle(self, *args, **options):
        succeeded, failed = export_all_configured()
        self.stdout.write('Course-completion exports: succeeded={} failed={}'.format(succeeded, failed))
        if failed:
            # Non-zero exit so a cron wrapper / alerting can flag partial failures.
            raise SystemExit(1)
