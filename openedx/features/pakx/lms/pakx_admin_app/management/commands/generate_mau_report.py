"""
Management command to (re)generate Monthly Active Users (MAU) reports on demand,
for testing and historical backfill.

Usage:
    ./manage.py lms generate_mau_report                        # previous month: all orgs + overall
    ./manage.py lms generate_mau_report --year 2026 --month 6
    ./manage.py lms generate_mau_report --year 2026 --month 6 --org arbisoft
    ./manage.py lms generate_mau_report --year 2026 --month 6 --overall-only
"""
from django.core.management.base import BaseCommand, CommandError

from organizations.models import Organization

from ...mau import generate_all_mau_reports, generate_mau_report, get_previous_month


class Command(BaseCommand):
    help = 'Generate Monthly Active Users (MAU) reports for a given month.'

    def add_arguments(self, parser):
        parser.add_argument('--year', type=int, help='Report year (defaults to the previous month).')
        parser.add_argument('--month', type=int, help='Report month 1-12 (defaults to the previous month).')
        parser.add_argument('--org', help='Org short_name to generate a single org report for.')
        parser.add_argument(
            '--overall-only', action='store_true',
            help='Generate only the overall all-organizations report.',
        )

    def handle(self, *args, **options):
        year = options.get('year')
        month = options.get('month')
        if (year is None) != (month is None):
            raise CommandError('Provide both --year and --month, or neither.')
        if year is None:
            year, month = get_previous_month()
        if not 1 <= month <= 12:
            raise CommandError('--month must be between 1 and 12.')

        org_short_name = options.get('org')
        overall_only = options.get('overall_only')

        if org_short_name:
            organization = Organization.objects.filter(short_name__iexact=org_short_name).first()
            if not organization:
                raise CommandError('No organization with short_name "{}".'.format(org_short_name))
            report = generate_mau_report(year, month, organization=organization)
            self._echo(report)
        elif overall_only:
            report = generate_mau_report(year, month, organization=None)
            self._echo(report)
        else:
            reports = generate_all_mau_reports(year, month)
            self.stdout.write('Generated {} reports for {:04d}-{:02d}.'.format(len(reports), year, month))

    def _echo(self, report):
        self.stdout.write('Generated {}: {} active learners, {} excluded staff.'.format(
            report, report.active_user_count, report.excluded_staff_count,
        ))
