"""
Management command to email learners a link to buy the verified certificate for the courses they completed.

The CSV needs a course_id column and a user_id or email column, such as the Studio enrollment export.
Each learner gets one email listing their eligible courses.

Example:
    ./manage.py lms send_verified_upgrade_email --csv /tmp/completions.csv --dry-run --report /tmp/report.csv
    ./manage.py lms send_verified_upgrade_email --csv /tmp/completions.csv --test-email qa@example.com
    ./manage.py lms send_verified_upgrade_email --csv /tmp/completions.csv --max-courses 3 --min-progress 100
"""

import csv
import io
from logging import getLogger

from django.core.management.base import BaseCommand, CommandError

from lms.djangoapps.commerce.utils import EcommerceService
from openedx.features.pakx.lms.overrides.tasks import send_verified_upgrade_emails
from openedx.features.pakx.lms.overrides.upgrade_campaign import (
    DEFAULT_HERO_IMAGE_URL,
    build_payload,
    evaluate_rows,
    group_into_emails,
    read_input_rows
)

log = getLogger(__name__)

BATCH_SIZE = 500
REPORT_COLUMNS = ['line', 'user_id', 'email', 'course_id', 'result', 'reason', 'in_email']


class Command(BaseCommand):
    """
    Email each learner in a CSV of course completions a link to buy the verified certificate.

    Rows that are not eligible are skipped with a reason. Use --dry-run to check a file without sending anything,
    and --test-email to send a single sample email to one address instead of to the learners.
    """

    help = 'Email learners a link to buy the verified certificate for the courses they completed'

    def add_arguments(self, parser):
        parser.add_argument('--csv', required=True, help='CSV file with a course_id and a user_id or email column')
        parser.add_argument('--dry-run', action='store_true', default=False,
                            help='Check the file and write the report, but send nothing')
        parser.add_argument('--test-email', help='Send one sample email to this address instead of to the learners')
        parser.add_argument('--report', help='Write a CSV report with the outcome of every row to this path')
        parser.add_argument('--hero-image-url', default=DEFAULT_HERO_IMAGE_URL,
                            help='URL of the image at the top of the email')
        parser.add_argument('--max-courses', type=int, default=3,
                            help='Most courses shown as cards in one email, the rest are counted (default: 3)')
        parser.add_argument('--min-progress', type=int, default=100,
                            help='Lowest course progress, in percent, that makes a row eligible (default: 100)')

    def handle(self, *args, **options):
        if not EcommerceService().config.checkout_on_ecommerce_service:
            raise CommandError('Ecommerce checkout is disabled for this site; nothing was sent.')

        test_email = options['test_email']
        if test_email is not None:
            test_email = test_email.strip()
            if '@' not in test_email:
                raise CommandError('--test-email needs an email address.')
        if options['max_courses'] < 1:
            raise CommandError('--max-courses must be at least 1.')
        if not 0 <= options['min_progress'] <= 100:
            raise CommandError('--min-progress must be between 0 and 100.')

        rows = self._read_rows(options['csv'])
        results = evaluate_rows(rows, min_progress=options['min_progress'])
        learner_emails = group_into_emails(results, max_courses=options['max_courses'])

        dry_run = options['dry_run']
        hero_image_url = options['hero_image_url']

        if test_email is not None:
            if not learner_emails:
                raise CommandError('No eligible learner to build a test email from.')
            # max() keeps the first learner when several have the same number of courses
            sample = max(learner_emails, key=lambda email: len(email.courses) + email.more_count)
            payloads = [build_payload(sample, hero_image_url, to_email=test_email)]
        else:
            payloads = [build_payload(learner_email, hero_image_url) for learner_email in learner_emails]

        # Nothing goes to the learners on a dry run or when only the test address is mailed
        sent_label = 'dry-run' if dry_run or test_email is not None else 'sent'

        eligible = sum(1 for result in results if not result.reason)
        summary = 'Rows: {rows}, eligible: {eligible}, skipped: {skipped}, emails: {emails}'.format(
            rows=len(results), eligible=eligible, skipped=len(results) - eligible, emails=len(learner_emails)
        )
        log.info(summary)
        self.stdout.write(summary)

        # The report is written before anything is queued, so a bad report path stops the run with nothing sent
        # and a failure while queueing still leaves the report behind
        if options['report']:
            self._write_report(options['report'], self._report_rows(results, learner_emails, sent_label))

        if not dry_run:
            self._queue_batches(payloads)

    @staticmethod
    def _queue_batches(payloads):
        """Queue the payloads in batches, logging each batch so a failure part way leaves a record of what was queued"""
        total = (len(payloads) + BATCH_SIZE - 1) // BATCH_SIZE
        for number, start in enumerate(range(0, len(payloads), BATCH_SIZE), 1):
            batch = payloads[start:start + BATCH_SIZE]
            try:
                send_verified_upgrade_emails.delay(batch)
            except Exception as error:
                if number == 1:
                    raise CommandError(
                        'Queueing failed at batch 1 of {total}; no batch was queued. Cause: {error}'.format(
                            total=total, error=error
                        )
                    )
                raise CommandError(
                    'Queueing failed at batch {number} of {total}; batches 1..{queued} were queued. '
                    'Do not rerun the whole list: remove the learners in the queued batches first. '
                    'Cause: {error}'.format(number=number, total=total, queued=number - 1, error=error)
                )
            log.info('Queued batch {number} of {total}: {count} emails, user ids {first}..{last}'.format(
                number=number, total=total, count=len(batch),
                first=batch[0]['user_id'], last=batch[-1]['user_id'],
            ))

    @staticmethod
    def _read_rows(csv_path):
        """Return the input rows of the CSV file, turning an unreadable or malformed file into a CommandError"""
        try:
            with io.open(csv_path, encoding='utf-8-sig', newline='') as csv_file:
                return read_input_rows(csv_file)
        except (IOError, OSError) as error:
            raise CommandError('Could not read {path}: {error}'.format(path=csv_path, error=error))
        except ValueError as error:
            raise CommandError(str(error))

    @staticmethod
    def _report_rows(results, learner_emails, sent_label):
        """Return the report as a list of dicts, one per input row in input order"""
        in_cards = {
            (learner_email.user.id, course.course_key)
            for learner_email in learner_emails for course in learner_email.courses
        }
        report_rows = []
        for result in results:
            row = result.row
            if result.reason:
                outcome, in_email = 'skipped', ''
            else:
                outcome = sent_label
                in_email = 'yes' if (result.user.id, result.course_key) in in_cards else 'more'
            report_rows.append({
                'line': row.line,
                'user_id': '' if row.user_id is None else row.user_id,
                'email': row.email or '',
                'course_id': row.course_id,
                'result': outcome,
                'reason': result.reason,
                'in_email': in_email,
            })
        return report_rows

    @staticmethod
    def _write_report(report_path, report_rows):
        """Write the report rows to a CSV file"""
        try:
            with io.open(report_path, 'w', encoding='utf-8', newline='') as report_file:
                writer = csv.DictWriter(report_file, fieldnames=REPORT_COLUMNS)
                writer.writeheader()
                writer.writerows(report_rows)
        except (IOError, OSError) as error:
            raise CommandError('Could not write the report to {path}, nothing was sent: {error}'.format(
                path=report_path, error=error
            ))
