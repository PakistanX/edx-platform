"""
Delete old per-visitor rows of tracked course links.

CourseLinkVisitor holds one row per (link, anonymous visitor, day) and is only needed to count distinct
visitors over a reporting period (capped at MAX_REPORT_DAYS) and over the previous period of the same
length, which reports compare it with. Rows older than --days (default 733, twice that cap) therefore no longer
affect any report; the aggregated CourseLinkDailyStat rows (hits and daily unique visitors) are kept forever.

Run it from cron (celery-beat is not running in production), e.g. weekly:
    0 3 * * 0  /edx/app/edxapp/edx-platform/manage.py lms prune_course_link_visitors
"""
import logging
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError

from openedx.features.pakx.lms.pakx_admin_app.course_links import VISITOR_RETENTION_DAYS, today
from openedx.features.pakx.lms.pakx_admin_app.models import CourseLinkVisitor

log = logging.getLogger(__name__)
BATCH_SIZE = 5000


class Command(BaseCommand):
    """Prune CourseLinkVisitor rows older than --days."""
    help = 'Delete per-visitor course link rows older than --days (daily stats are kept).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days', type=int, default=VISITOR_RETENTION_DAYS, help='Keep this many days of visitor rows.'
        )
        parser.add_argument('--dry-run', action='store_true', help='Only report how many rows would go.')

    def handle(self, *args, **options):
        days = options['days']
        if days < VISITOR_RETENTION_DAYS:
            raise CommandError(
                '--days must be at least {}, or period-wide unique visitor counts and the '
                'previous-period comparison would shrink.'.format(VISITOR_RETENTION_DAYS)
            )
        cutoff = today() - timedelta(days=days)
        old_rows = CourseLinkVisitor.objects.filter(date__lt=cutoff)
        if options['dry_run']:
            self.stdout.write('Would delete {} visitor row(s) older than {}.'.format(old_rows.count(), cutoff))
            return
        deleted = 0
        while True:
            batch_ids = list(old_rows.values_list('pk', flat=True)[:BATCH_SIZE])  # small batches keep locks short
            if not batch_ids:
                break
            CourseLinkVisitor.objects.filter(pk__in=batch_ids).delete()
            deleted += len(batch_ids)
        log.info('Pruned %s course link visitor row(s) older than %s', deleted, cutoff)
        self.stdout.write('Deleted {} visitor row(s) older than {}.'.format(deleted, cutoff))
