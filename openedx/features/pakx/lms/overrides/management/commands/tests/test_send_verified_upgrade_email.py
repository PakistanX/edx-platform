"""Tests for the send_verified_upgrade_email management command."""

import csv
import io
import os
import shutil
import tempfile

import mock
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from lms.djangoapps.commerce.models import CommerceConfiguration
from openedx.core.djangolib.testing.utils import skip_unless_lms
from openedx.features.pakx.lms.overrides.test_upgrade_campaign import CampaignDataMixin
from openedx.features.pakx.lms.overrides.upgrade_campaign import DEFAULT_HERO_IMAGE_URL
from student.tests.factories import UserFactory

COMMAND = 'send_verified_upgrade_email'
COMMAND_MODULE = 'openedx.features.pakx.lms.overrides.management.commands.send_verified_upgrade_email'
REPORT_COLUMNS = ['line', 'user_id', 'email', 'course_id', 'result', 'reason', 'in_email']


@mock.patch(COMMAND_MODULE + '.send_verified_upgrade_emails')
# DISCOVER_URL is only set by deployment config; the URL conf needs it when it loads under lms.envs.test
@override_settings(DISCOVER_URL='https://discover.example.com')
@skip_unless_lms
class SendVerifiedUpgradeEmailTests(CampaignDataMixin, TestCase):
    """
    Tests for the send_verified_upgrade_email command.

    The Celery task is mocked, so the tests assert on the payload batches handed to ``.delay``.
    """

    def setUp(self):
        super(SendVerifiedUpgradeEmailTests, self).setUp()
        CommerceConfiguration.objects.create(checkout_on_ecommerce_service=True)
        self.work_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.work_dir)
        self.report_path = os.path.join(self.work_dir, 'report.csv')

        self.user = UserFactory(email='ayesha@x.com')
        self.course_a = self.make_course(title='Machine Learning')
        self.course_b = self.make_course(title='Statistics')
        self.course_c = self.make_course(title='Algebra')
        self.enroll(self.user, self.course_a, progress=100)
        self.enroll(self.user, self.course_b, progress=100)
        self.enroll(self.user, self.course_c, progress=50)
        self.csv_path = self.write_csv([
            (self.user.id, '', self.course_a),
            (self.user.id, '', self.course_b),
            (self.user.id, '', self.course_c),
        ])

    def write_csv(self, records, name='input.csv', header='user_id,email,course_id\n'):
        """Write a CSV of (user id, email, course key) records and return its path."""
        path = os.path.join(self.work_dir, name)
        with io.open(path, 'w', encoding='utf-8') as csv_file:
            csv_file.write(u'\ufeff' + header)  # Studio exports start with a byte order mark
            for user_id, email, course_key in records:
                csv_file.write(u'{},{},{}\n'.format(user_id, email, course_key))
        return path

    def read_report(self):
        """Return the report rows as a list of dicts."""
        with io.open(self.report_path, encoding='utf-8', newline='') as report_file:
            reader = csv.DictReader(report_file)
            assert reader.fieldnames == REPORT_COLUMNS
            return list(reader)

    @staticmethod
    def batches(mock_task):
        """Return the payload batches passed to the task, in call order."""
        return [call[0][0] for call in mock_task.delay.call_args_list]

    def test_sends_one_email_per_learner(self, mock_task):
        call_command(COMMAND, '--csv', self.csv_path, '--report', self.report_path)

        [batch] = self.batches(mock_task)
        assert len(batch) == 1
        assert batch[0]['user_id'] == self.user.id
        assert batch[0]['to_email'] == 'ayesha@x.com'
        assert len(batch[0]['context']['courses']) == 2
        assert batch[0]['context']['hero_image_url'] == DEFAULT_HERO_IMAGE_URL
        assert [r['result'] for r in self.read_report()] == ['sent', 'sent', 'skipped']

    def test_report_has_the_row_details(self, mock_task):
        call_command(COMMAND, '--csv', self.csv_path, '--report', self.report_path)

        report = self.read_report()
        assert report[0] == {
            'line': '2', 'user_id': str(self.user.id), 'email': '', 'course_id': str(self.course_a),
            'result': 'sent', 'reason': '', 'in_email': 'yes',
        }
        assert report[2]['reason'] == 'progress below 100'
        assert report[2]['in_email'] == ''

    def test_report_for_email_only_rows(self, mock_task):
        path = self.write_csv([('', 'AYESHA@x.com', self.course_a)], header='user_id,email,course_id\n')
        call_command(COMMAND, '--csv', path, '--report', self.report_path)

        [row] = self.read_report()
        assert (row['user_id'], row['email'], row['result']) == ('', 'AYESHA@x.com', 'sent')

    def test_dry_run_sends_nothing(self, mock_task):
        call_command(COMMAND, '--csv', self.csv_path, '--dry-run', '--report', self.report_path)

        mock_task.delay.assert_not_called()
        report = self.read_report()
        assert [r['result'] for r in report] == ['dry-run', 'dry-run', 'skipped']
        assert [r['in_email'] for r in report] == ['yes', 'yes', '']

    def test_test_email_sends_only_to_that_address(self, mock_task):
        call_command(COMMAND, '--csv', self.csv_path, '--test-email', 'qa@x.com', '--report', self.report_path)

        [[payload]] = self.batches(mock_task)
        assert payload['to_email'] == 'qa@x.com'
        assert payload['user_id'] == self.user.id
        # Nothing went to the learner, so the report says so
        report = self.read_report()
        assert [r['result'] for r in report] == ['dry-run', 'dry-run', 'skipped']
        assert [r['in_email'] for r in report] == ['yes', 'yes', '']

    def test_test_email_uses_the_learner_with_most_eligible_courses(self, mock_task):
        other = UserFactory(email='bilal@x.com')
        other_course = self.make_course(title='Physics')
        self.enroll(other, other_course, progress=100)
        path = self.write_csv([
            (other.id, '', other_course),
            (self.user.id, '', self.course_a),
            (self.user.id, '', self.course_b),
        ])
        call_command(COMMAND, '--csv', path, '--test-email', 'qa@x.com')

        [[payload]] = self.batches(mock_task)
        assert payload['user_id'] == self.user.id

    def test_test_email_with_dry_run_sends_nothing(self, mock_task):
        call_command(COMMAND, '--csv', self.csv_path, '--test-email', 'qa@x.com', '--dry-run')
        mock_task.delay.assert_not_called()

    def test_test_email_needs_an_eligible_learner(self, mock_task):
        path = self.write_csv([(self.user.id, '', self.course_c)])
        with self.assertRaisesRegex(CommandError, 'No eligible learner to build a test email from.'):
            call_command(COMMAND, '--csv', path, '--test-email', 'qa@x.com')
        mock_task.delay.assert_not_called()

    def test_nothing_is_queued_when_no_row_is_eligible(self, mock_task):
        path = self.write_csv([(self.user.id, '', self.course_c)])
        call_command(COMMAND, '--csv', path, '--report', self.report_path)

        mock_task.delay.assert_not_called()
        assert [r['result'] for r in self.read_report()] == ['skipped']

    def test_batches_of_500(self, mock_task):
        learner_emails = [mock.Mock(courses=[], more_count=0) for index in range(501)]
        with mock.patch(COMMAND_MODULE + '.evaluate_rows', return_value=[]), \
                mock.patch(COMMAND_MODULE + '.group_into_emails', return_value=learner_emails), \
                mock.patch(COMMAND_MODULE + '.build_payload', side_effect=lambda email, hero: {'learner': email, 'user_id': 1}):
            call_command(COMMAND, '--csv', self.csv_path)

        batches = self.batches(mock_task)
        assert [len(batch) for batch in batches] == [500, 1]
        assert [payload['learner'] for batch in batches for payload in batch] == learner_emails

    def test_report_marks_courses_beyond_cap(self, mock_task):
        call_command(COMMAND, '--csv', self.csv_path, '--max-courses', '1', '--report', self.report_path)

        [batch] = self.batches(mock_task)
        assert len(batch[0]['context']['courses']) == 1
        assert batch[0]['context']['more_count'] == 1
        assert [r['in_email'] for r in self.read_report()] == ['yes', 'more', '']

    def test_min_progress_option(self, mock_task):
        call_command(COMMAND, '--csv', self.csv_path, '--min-progress', '50', '--report', self.report_path)

        [batch] = self.batches(mock_task)
        assert len(batch[0]['context']['courses']) == 3
        assert [r['result'] for r in self.read_report()] == ['sent', 'sent', 'sent']

    def test_hero_image_url_option(self, mock_task):
        call_command(COMMAND, '--csv', self.csv_path, '--hero-image-url', 'https://x.com/hero.png')

        [batch] = self.batches(mock_task)
        assert batch[0]['context']['hero_image_url'] == 'https://x.com/hero.png'

    def test_summary_is_logged(self, mock_task):
        with mock.patch(COMMAND_MODULE + '.log') as mock_log:
            call_command(COMMAND, '--csv', self.csv_path)
        mock_log.info.assert_any_call('Rows: 3, eligible: 2, skipped: 1, emails: 1')

    def test_stops_when_ecommerce_is_off(self, mock_task):
        CommerceConfiguration.objects.create(checkout_on_ecommerce_service=False)
        with self.assertRaisesRegex(CommandError, 'Ecommerce checkout is disabled'):
            call_command(COMMAND, '--csv', self.csv_path)
        mock_task.delay.assert_not_called()

    def test_ecommerce_is_checked_before_reading_the_csv(self, mock_task):
        CommerceConfiguration.objects.create(checkout_on_ecommerce_service=False)
        with self.assertRaisesRegex(CommandError, 'Ecommerce checkout is disabled'):
            call_command(COMMAND, '--csv', os.path.join(self.work_dir, 'missing.csv'), '--dry-run')

    def test_bad_report_path_stops_before_anything_is_queued(self, mock_task):
        report_path = os.path.join(self.work_dir, 'missing_dir', 'report.csv')
        with self.assertRaisesRegex(CommandError, 'Could not write the report'):
            call_command(COMMAND, '--csv', self.csv_path, '--report', report_path)
        mock_task.delay.assert_not_called()

    def test_report_is_written_even_if_queueing_fails(self, mock_task):
        mock_task.delay.side_effect = RuntimeError('broker down')
        with self.assertRaisesRegex(CommandError, 'Queueing failed at batch 1 of 1; no batch was queued. Cause: broker down'):
            call_command(COMMAND, '--csv', self.csv_path, '--report', self.report_path)
        assert [r['result'] for r in self.read_report()] == ['sent', 'sent', 'skipped']

    def test_blank_test_email_is_rejected_and_nothing_is_queued(self, mock_task):
        for value in ('', '   ', 'not-an-address'):
            with self.assertRaisesRegex(CommandError, '--test-email needs an email address.'):
                call_command(COMMAND, '--csv', self.csv_path, '--test-email', value)
        mock_task.delay.assert_not_called()

    def test_test_email_is_stripped(self, mock_task):
        call_command(COMMAND, '--csv', self.csv_path, '--test-email', ' qa@x.com ')
        [[payload]] = self.batches(mock_task)
        assert payload['to_email'] == 'qa@x.com'

    def test_max_courses_must_be_at_least_one(self, mock_task):
        with self.assertRaisesRegex(CommandError, '--max-courses must be at least 1.'):
            call_command(COMMAND, '--csv', self.csv_path, '--max-courses', '0')
        mock_task.delay.assert_not_called()

    def test_min_progress_must_be_between_0_and_100(self, mock_task):
        for value in ('-1', '101'):
            with self.assertRaisesRegex(CommandError, '--min-progress must be between 0 and 100.'):
                call_command(COMMAND, '--csv', self.csv_path, '--min-progress', value)
        mock_task.delay.assert_not_called()

    def test_range_checks_run_before_reading_the_csv(self, mock_task):
        with self.assertRaisesRegex(CommandError, '--max-courses must be at least 1.'):
            call_command(COMMAND, '--csv', os.path.join(self.work_dir, 'missing.csv'), '--max-courses', '0')

    def test_each_batch_is_logged(self, mock_task):
        learner_emails = [mock.Mock(courses=[], more_count=0) for index in range(501)]
        payloads = [{'user_id': index + 10} for index in range(501)]
        with mock.patch(COMMAND_MODULE + '.evaluate_rows', return_value=[]), \
                mock.patch(COMMAND_MODULE + '.group_into_emails', return_value=learner_emails), \
                mock.patch(COMMAND_MODULE + '.build_payload', side_effect=payloads), \
                mock.patch(COMMAND_MODULE + '.log') as mock_log:
            call_command(COMMAND, '--csv', self.csv_path)
        assert [call[0][0] for call in mock_log.info.call_args_list[1:]] == [
            'Queued batch 1 of 2: 500 emails, user ids 10..509',
            'Queued batch 2 of 2: 1 emails, user ids 510..510',
        ]

    def test_failure_on_a_later_batch_names_the_batch(self, mock_task):
        learner_emails = [mock.Mock(courses=[], more_count=0) for index in range(501)]
        payloads = [{'user_id': index + 10} for index in range(501)]
        mock_task.delay.side_effect = [None, RuntimeError('broker down')]
        with mock.patch(COMMAND_MODULE + '.evaluate_rows', return_value=[]), \
                mock.patch(COMMAND_MODULE + '.group_into_emails', return_value=learner_emails), \
                mock.patch(COMMAND_MODULE + '.build_payload', side_effect=payloads), \
                mock.patch(COMMAND_MODULE + '.log') as mock_log:
            with self.assertRaisesRegex(CommandError, 'Queueing failed at batch 2 of 2; batches 1..1 were queued. '
                                                      'Do not rerun the whole list.*Cause: broker down'):
                call_command(COMMAND, '--csv', self.csv_path)
        mock_log.info.assert_any_call('Queued batch 1 of 2: 500 emails, user ids 10..509')

    def test_bad_csv_is_a_command_error(self, mock_task):
        bad_path = self.write_csv([], name='bad.csv', header='user_id,mode\n')
        with self.assertRaisesRegex(CommandError, 'CSV needs a course_id column'):
            call_command(COMMAND, '--csv', bad_path)
        mock_task.delay.assert_not_called()

    def test_non_numeric_user_id_is_a_command_error(self, mock_task):
        path = self.write_csv([('abc', '', self.course_a)])
        with self.assertRaisesRegex(CommandError, 'Line 2: user_id "abc" is not a number'):
            call_command(COMMAND, '--csv', path)

    def test_missing_csv_file_is_a_command_error(self, mock_task):
        with self.assertRaisesRegex(CommandError, 'Could not read'):
            call_command(COMMAND, '--csv', os.path.join(self.work_dir, 'missing.csv'))
