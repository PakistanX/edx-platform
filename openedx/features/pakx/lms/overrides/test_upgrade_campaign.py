"""Tests for the verified certificate upgrade campaign helpers."""

import io
import json
from datetime import timedelta

import ddt
import mock
from django.test import TestCase, override_settings
from django.utils import timezone
from opaque_keys.edx.keys import CourseKey

from bulk_email.models import Optout
from course_modes.models import CourseMode
from course_modes.tests.factories import CourseModeFactory
from lms.djangoapps.verify_student.models import VerificationDeadline
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms
from openedx.features.pakx.lms.overrides.models import CourseProgressStats
from openedx.features.pakx.lms.overrides.tasks import send_verified_upgrade_emails
from openedx.features.pakx.lms.overrides.upgrade_campaign import (
    DEFAULT_HERO_IMAGE_URL,
    InputRow,
    LearnerEmail,
    build_email_context,
    build_payload,
    build_upgrade_url,
    cta_text,
    evaluate_rows,
    group_into_emails,
    read_input_rows
)
from student.tests.factories import CourseEnrollmentFactory, UserFactory


@skip_unless_lms
class ReadInputRowsTests(TestCase):
    """Tests for reading the campaign CSV."""

    def test_reads_user_id_and_email_columns(self):
        rows = read_input_rows(io.StringIO('user_id,email,course_id,mode\n7,a@x.com,course-v1:A+1+2024,honor\n'))
        assert rows == [InputRow(2, 7, 'a@x.com', 'course-v1:A+1+2024')]

    def test_email_only_column(self):
        rows = read_input_rows(io.StringIO('email,course_id\na@x.com,course-v1:A+1+2024\n'))
        assert rows == [InputRow(2, None, 'a@x.com', 'course-v1:A+1+2024')]

    def test_reads_bom_and_strips_whitespace(self):
        rows = read_input_rows(io.StringIO('\ufeffuser_id,course_id\n 7 , course-v1:A+1+2024 \n'))
        assert rows == [InputRow(2, 7, None, 'course-v1:A+1+2024')]

    def test_missing_columns_raise(self):
        with self.assertRaisesRegex(ValueError, 'CSV needs a course_id column'):
            read_input_rows(io.StringIO('user_id,mode\n7,honor\n'))

    def test_non_numeric_user_id_names_the_line(self):
        with self.assertRaises(ValueError) as context:
            read_input_rows(io.StringIO('user_id,course_id\nabc,course-v1:A+1+2024\n'))
        assert str(context.exception) == 'Line 2: user_id "abc" is not a number'


@skip_unless_lms
@ddt.ddt
class CtaTextTests(TestCase):
    """Tests for the button text bands."""

    @ddt.data(
        (100, 'Claim my certificate'),
        (50, 'Finish strong & get certified'),
        (99.6, 'Finish strong & get certified'),
        (1, 'Keep going & get certified'),
        (0, 'Start my certificate journey'),
    )
    @ddt.unpack
    def test_bands(self, progress, text):
        assert cta_text(progress) == text


@skip_unless_lms
class UpgradeUrlTests(TestCase):
    """Tests for the upgrade link."""

    # DISCOVER_URL is only set by deployment config; the URL conf needs it when it loads under lms.envs.test
    @override_settings(LMS_ROOT_URL='https://ilmx.org', DISCOVER_URL='https://discover.example.com')
    def test_url(self):
        url = build_upgrade_url(CourseKey.from_string('course-v1:Amal_Academy+8+2024'))
        assert url == ('https://ilmx.org/verify_student/upgrade/course-v1:Amal_Academy+8+2024/'
                       '?utm_source=email&utm_medium=marketing&utm_campaign=verified_upgrade')


class CampaignDataMixin(object):
    """Builds learners, courses and enrollments for the eligibility tests."""

    def make_course(self, title='Machine Learning', sku='SKU1', price=49):
        """Create a course overview, with a verified seat for sale when a sku is given."""
        self.course_count = getattr(self, 'course_count', 0) + 1
        course_key = CourseKey.from_string('course-v1:Amal+C{}+2024'.format(self.course_count))
        CourseOverviewFactory(id=course_key, display_name=title)
        if sku is not None:
            CourseModeFactory(course_id=course_key, mode_slug='verified', sku=sku, min_price=price)
        return course_key

    def enroll(self, user, course_key, progress=100, mode='honor', is_active=True):
        """Enroll the user in the course and record the course progress, unless it is None."""
        enrollment = CourseEnrollmentFactory(user=user, course_id=course_key, mode=mode, is_active=is_active)
        if progress is not None:
            CourseProgressStats.objects.create(enrollment=enrollment, progress=progress)
        return enrollment

    @staticmethod
    def row(user, course_key, line=2):
        """Return an input row that names the user by id."""
        return InputRow(line, user.id, None, str(course_key))


@skip_unless_lms
@ddt.ddt
class EvaluateRowsTests(CampaignDataMixin, TestCase):
    """Tests for the eligibility checks."""

    def setUp(self):
        super(EvaluateRowsTests, self).setUp()
        self.user = UserFactory(email='ayesha@x.com')
        self.course_key = self.make_course()
        self.enrollment = self.enroll(self.user, self.course_key)

    def evaluate_one(self, row, **kwargs):
        """Evaluate a single row and return its result."""
        [result] = evaluate_rows([row], **kwargs)
        return result

    def test_completed_honor_learner_is_eligible(self):
        [result] = evaluate_rows([InputRow(2, self.user.id, None, str(self.course_key))])
        assert result.reason == '' and result.progress == 100 and result.course_title == 'Machine Learning'
        assert result.user == self.user and result.course_key == self.course_key

    def test_results_keep_the_row_order(self):
        other_course = self.make_course(title='Statistics')
        self.enroll(self.user, other_course, progress=50)
        rows = [self.row(self.user, other_course, line=2), self.row(self.user, self.course_key, line=3)]
        results = evaluate_rows(rows)
        assert [r.row for r in results] == rows
        assert [r.reason for r in results] == ['progress below 100', '']

    @ddt.data(
        ('verified', 'already paid'), ('professional', 'already paid'), ('no-id-professional', 'already paid'),
        ('credit', 'already paid'), ('masters', 'already paid'),
    )
    @ddt.unpack
    def test_paid_modes_are_skipped(self, mode, reason):
        self.enrollment.mode = mode
        self.enrollment.save()
        assert self.evaluate_one(self.row(self.user, self.course_key)).reason == reason

    def test_user_not_found(self):
        result = self.evaluate_one(InputRow(2, self.user.id + 1000, None, str(self.course_key)))
        assert result.reason == 'user not found' and result.user is None
        assert self.evaluate_one(InputRow(2, None, 'nobody@x.com', str(self.course_key))).reason == 'user not found'
        assert self.evaluate_one(InputRow(2, None, None, str(self.course_key))).reason == 'user not found'

    def test_invalid_course_id_is_reported(self):
        result = self.evaluate_one(InputRow(2, self.user.id, None, 'not-a-key'))
        assert result.reason == 'invalid course id' and result.course_key is None

    def test_invalid_course_id_does_not_stop_other_rows(self):
        results = evaluate_rows([InputRow(2, self.user.id, None, 'not-a-key'), self.row(self.user, self.course_key, 3)])
        assert [r.reason for r in results] == ['invalid course id', '']

    def test_course_not_found(self):
        missing = CourseKey.from_string('course-v1:Amal+Missing+2024')
        result = self.evaluate_one(self.row(self.user, missing))
        assert result.reason == 'course not found' and result.course_key == missing and result.user == self.user

    def test_not_enrolled(self):
        other_course = self.make_course()
        result = self.evaluate_one(self.row(self.user, other_course))
        assert result.reason == 'not enrolled' and result.course_title == 'Machine Learning'

    def test_enrollment_inactive(self):
        self.enrollment.is_active = False
        self.enrollment.save()
        assert self.evaluate_one(self.row(self.user, self.course_key)).reason == 'enrollment inactive'

    def test_account_inactive(self):
        self.user.is_active = False
        self.user.save()
        assert self.evaluate_one(self.row(self.user, self.course_key)).reason == 'account inactive'

    def test_no_verified_seat_for_sale(self):
        no_seat = self.make_course(sku=None)
        self.enroll(self.user, no_seat)
        assert self.evaluate_one(self.row(self.user, no_seat)).reason == 'no verified seat for sale'

    def test_verified_seat_without_sku_is_not_for_sale(self):
        no_sku = self.make_course(sku='')
        self.enroll(self.user, no_sku)
        assert self.evaluate_one(self.row(self.user, no_sku)).reason == 'no verified seat for sale'

    def test_verified_seat_priced_at_zero_is_not_for_sale(self):
        free_seat = self.make_course()
        # Model validation rejects a verified price of 0, so set it behind its back as bad data would be
        CourseMode.objects.filter(course_id=free_seat).update(min_price=0)
        self.enroll(self.user, free_seat)
        assert self.evaluate_one(self.row(self.user, free_seat)).reason == 'no verified seat for sale'

    def test_verification_deadline_passed(self):
        VerificationDeadline.set_deadline(self.course_key, timezone.now() - timedelta(days=1))
        assert self.evaluate_one(self.row(self.user, self.course_key)).reason == 'verification deadline passed'

    def test_future_verification_deadline_is_eligible(self):
        VerificationDeadline.set_deadline(self.course_key, timezone.now() + timedelta(days=1))
        assert self.evaluate_one(self.row(self.user, self.course_key)).reason == ''

    def test_opted_out(self):
        Optout.objects.create(user=self.user, course_id=self.course_key)
        assert self.evaluate_one(self.row(self.user, self.course_key)).reason == 'opted out'

    def test_progress_below_minimum(self):
        CourseProgressStats.objects.filter(enrollment=self.enrollment).update(progress=83)
        result = self.evaluate_one(self.row(self.user, self.course_key))
        assert result.reason == 'progress below 100' and result.progress == 83
        assert self.evaluate_one(self.row(self.user, self.course_key), min_progress=50).reason == ''

    def test_missing_progress_row_counts_as_zero(self):
        CourseProgressStats.objects.filter(enrollment=self.enrollment).delete()
        result = self.evaluate_one(self.row(self.user, self.course_key))
        assert result.reason == 'progress below 100' and result.progress == 0

    def test_first_failing_check_wins(self):
        self.enrollment.is_active = False
        self.enrollment.mode = 'verified'
        self.enrollment.save()
        Optout.objects.create(user=self.user, course_id=self.course_key)
        assert self.evaluate_one(self.row(self.user, self.course_key)).reason == 'enrollment inactive'

    def test_email_match_is_case_insensitive(self):
        result = self.evaluate_one(InputRow(2, None, 'Ayesha@X.com', str(self.course_key)))
        assert result.reason == '' and result.user == self.user

    def test_user_id_wins_over_email(self):
        other = UserFactory(email='sana@x.com')
        result = self.evaluate_one(InputRow(2, self.user.id, other.email, str(self.course_key)))
        assert result.user == self.user and result.reason == ''

    def test_duplicate_row(self):
        row = self.row(self.user, self.course_key)
        first, second = evaluate_rows([row, row._replace(line=3)])
        assert first.reason == '' and second.reason == 'duplicate row'

    def test_same_learner_named_by_id_and_by_email_is_a_duplicate(self):
        by_id = self.row(self.user, self.course_key)
        by_email = InputRow(3, None, 'AYESHA@x.com', str(self.course_key))
        first, second = evaluate_rows([by_id, by_email])
        assert first.reason == '' and second.reason == 'duplicate row' and second.user == self.user


@skip_unless_lms
class GroupIntoEmailsTests(CampaignDataMixin, TestCase):
    """Tests for grouping eligible rows into one email per learner."""

    def setUp(self):
        super(GroupIntoEmailsTests, self).setUp()
        self.user = UserFactory()

    def test_opt_out_drops_only_that_course(self):
        course_a, course_b, course_c = [self.make_course(title=name) for name in ('A', 'B', 'C')]
        for course in (course_a, course_b, course_c):
            self.enroll(self.user, course)
        Optout.objects.create(user=self.user, course_id=course_b)
        results = evaluate_rows([self.row(self.user, course) for course in (course_a, course_b, course_c)])
        [email] = group_into_emails(results)
        assert email.user == self.user
        assert [r.course_key for r in email.courses] == [course_a, course_c]
        assert email.more_count == 0

    def test_grouping_orders_by_progress_and_caps(self):
        courses = [self.make_course(title=str(progress)) for progress in (29, 100, 0, 83)]
        for course, progress in zip(courses, (29, 100, 0, 83)):
            self.enroll(self.user, course, progress=progress)
        results = evaluate_rows([self.row(self.user, course) for course in courses], min_progress=0)
        [email] = group_into_emails(results, max_courses=3)
        assert [r.progress for r in email.courses] == [100, 83, 29] and email.more_count == 1

    def test_default_cap_is_three_courses(self):
        courses = [self.make_course() for _ in range(5)]
        for course in courses:
            self.enroll(self.user, course)
        [email] = group_into_emails(evaluate_rows([self.row(self.user, course) for course in courses]))
        assert len(email.courses) == 3 and email.more_count == 2

    def test_one_email_per_learner_in_first_row_order(self):
        other = UserFactory()
        course = self.make_course()
        self.enroll(self.user, course)
        self.enroll(other, course)
        results = evaluate_rows([self.row(other, course, 2), self.row(self.user, course, 3)])
        assert [email.user for email in group_into_emails(results)] == [other, self.user]

    def test_learner_with_no_eligible_course_gets_no_email(self):
        course = self.make_course()
        self.enroll(self.user, course, progress=10)
        assert group_into_emails(evaluate_rows([self.row(self.user, course)])) == []


@skip_unless_lms
@override_settings(LMS_ROOT_URL='https://ilmx.org', DISCOVER_URL='https://discover.example.com')
class EmailContextTests(CampaignDataMixin, TestCase):
    """Tests for the email context and the payload sent to the Celery task."""

    def setUp(self):
        super(EmailContextTests, self).setUp()
        self.user = UserFactory(email='ayesha@x.com')
        self.user.profile.name = 'Ayesha Khan'
        self.user.profile.save()
        self.course_key = self.make_course(title='Machine Learning')
        self.course_key_2 = self.make_course(title='Statistics')
        self.enroll(self.user, self.course_key, progress=100)
        self.enroll(self.user, self.course_key_2, progress=60.4)
        results = evaluate_rows(
            [self.row(self.user, self.course_key), self.row(self.user, self.course_key_2, 3)], min_progress=0
        )
        [self.learner_email] = group_into_emails(results)

    def test_context_shape(self):
        ctx = build_email_context(self.learner_email)
        assert ctx['first_name'] == 'Ayesha'
        assert ctx['courses'][0] == {
            'title': 'Machine Learning', 'progress': 100, 'completed': True,
            'url': build_upgrade_url(self.course_key), 'button_text': 'Claim my certificate', 'primary': True,
        }
        assert ctx['courses'][1] == {
            'title': 'Statistics', 'progress': 60, 'completed': False,
            'url': build_upgrade_url(self.course_key_2), 'button_text': 'Finish strong & get certified',
            'primary': False,
        }
        assert [c['primary'] for c in ctx['courses']] == [True, False]
        assert ctx['course_ids'] == [str(self.course_key), str(self.course_key_2)]
        assert ctx['more_count'] == 0
        assert ctx['hero_image_url'] == DEFAULT_HERO_IMAGE_URL
        assert ctx['dashboard_url'] == 'https://ilmx.org/dashboard'
        assert 'price' not in json.dumps(ctx)

    def test_nearly_complete_course_is_not_shown_as_completed(self):
        course_key = self.make_course(title='Almost')
        self.enroll(self.user, course_key, progress=99.6)
        [learner_email] = group_into_emails(evaluate_rows([self.row(self.user, course_key)], min_progress=0))
        [card] = build_email_context(learner_email)['courses']
        assert card['progress'] == 99 and card['completed'] is False
        assert card['button_text'] == 'Finish strong & get certified'

    def test_context_is_json_serialisable(self):
        ctx = build_email_context(self.learner_email)
        assert json.loads(json.dumps(ctx)) == ctx

    def test_context_uses_the_given_hero_image_and_more_count(self):
        learner_email = LearnerEmail(self.learner_email.user, self.learner_email.courses, 2)
        ctx = build_email_context(learner_email, hero_image_url='https://img.example.com/hero.png')
        assert ctx['hero_image_url'] == 'https://img.example.com/hero.png' and ctx['more_count'] == 2

    def test_first_name_falls_back_to_username(self):
        self.user.profile.name = ''
        self.user.profile.save()
        assert build_email_context(self.learner_email)['first_name'] == self.user.username

    def test_first_name_falls_back_to_username_without_profile(self):
        self.user.profile.delete()
        user = type(self.user).objects.get(id=self.user.id)
        learner_email = LearnerEmail(user, self.learner_email.courses, 0)
        assert build_email_context(learner_email)['first_name'] == self.user.username

    def test_payload_defaults_to_the_learner_address(self):
        payload = build_payload(self.learner_email, DEFAULT_HERO_IMAGE_URL)
        assert payload['user_id'] == self.user.id and payload['to_email'] == 'ayesha@x.com'
        assert payload['context'] == build_email_context(self.learner_email)
        assert json.loads(json.dumps(payload)) == payload

    def test_payload_keeps_a_blank_test_address(self):
        payload = build_payload(self.learner_email, DEFAULT_HERO_IMAGE_URL, to_email='')
        assert payload['to_email'] == ''

    def test_payload_uses_the_test_address(self):
        payload = build_payload(self.learner_email, DEFAULT_HERO_IMAGE_URL, to_email='qa@x.com')
        assert payload['to_email'] == 'qa@x.com' and payload['user_id'] == self.user.id

    @mock.patch('openedx.features.pakx.lms.overrides.tasks.ace')
    def test_task_sends_one_message_per_payload(self, mock_ace):
        other = UserFactory(email='bilal@x.com')
        self.enroll(other, self.course_key)
        [other_email] = group_into_emails(evaluate_rows([self.row(other, self.course_key)]))
        send_verified_upgrade_emails([
            build_payload(self.learner_email, DEFAULT_HERO_IMAGE_URL),
            build_payload(other_email, DEFAULT_HERO_IMAGE_URL),
        ])
        assert mock_ace.send.call_count == 2
        msg = mock_ace.send.call_args_list[0][0][0]
        assert msg.name == 'verifiedupgradecampaign' and msg.recipient.email_address == self.user.email
        assert msg.recipient.username == self.user.username
        assert msg.context['courses'][0]['button_text'] == 'Claim my certificate'
        assert msg.context['course_ids'] == [str(self.course_key), str(self.course_key_2)]
        assert mock_ace.send.call_args_list[1][0][0].recipient.email_address == 'bilal@x.com'

    @mock.patch('openedx.features.pakx.lms.overrides.tasks.ace')
    def test_task_uses_test_address(self, mock_ace):
        send_verified_upgrade_emails([build_payload(self.learner_email, DEFAULT_HERO_IMAGE_URL, to_email='qa@x.com')])
        assert mock_ace.send.call_args[0][0].recipient.email_address == 'qa@x.com'

    @mock.patch('openedx.features.pakx.lms.overrides.tasks.log')
    @mock.patch('openedx.features.pakx.lms.overrides.tasks.ace')
    def test_task_skips_a_payload_whose_user_is_gone(self, mock_ace, mock_log):
        payload = build_payload(self.learner_email, DEFAULT_HERO_IMAGE_URL)
        payload['user_id'] = self.user.id + 1000
        send_verified_upgrade_emails([payload, build_payload(self.learner_email, DEFAULT_HERO_IMAGE_URL)])
        assert mock_ace.send.call_count == 1
        mock_log.warning.assert_called_once_with(
            'Verified upgrade email skipped, user {} does not exist'.format(self.user.id + 1000)
        )

    @mock.patch('openedx.features.pakx.lms.overrides.tasks.log')
    @mock.patch('openedx.features.pakx.lms.overrides.tasks.ace')
    def test_task_logs_a_summary(self, mock_ace, mock_log):
        other = UserFactory(email='bilal@x.com')
        self.enroll(other, self.course_key)
        [other_email] = group_into_emails(evaluate_rows([self.row(other, self.course_key)]))
        mock_ace.send.side_effect = [None, Exception('SMTP down')]
        send_verified_upgrade_emails([
            build_payload(self.learner_email, DEFAULT_HERO_IMAGE_URL),
            build_payload(other_email, DEFAULT_HERO_IMAGE_URL),
        ])
        mock_log.info.assert_called_once_with(
            'Verified upgrade emails: sent 1, failed 1 (user ids: {})'.format(other.id)
        )

    @mock.patch('openedx.features.pakx.lms.overrides.tasks.log')
    @mock.patch('openedx.features.pakx.lms.overrides.tasks.ace')
    def test_task_summary_without_failures_lists_no_ids(self, mock_ace, mock_log):
        send_verified_upgrade_emails([build_payload(self.learner_email, DEFAULT_HERO_IMAGE_URL)])
        mock_log.info.assert_called_once_with('Verified upgrade emails: sent 1, failed 0')

    @mock.patch('openedx.features.pakx.lms.overrides.tasks.log')
    @mock.patch('openedx.features.pakx.lms.overrides.tasks.ace')
    def test_task_continues_after_a_failed_send(self, mock_ace, mock_log):
        other = UserFactory(email='bilal@x.com')
        self.enroll(other, self.course_key)
        [other_email] = group_into_emails(evaluate_rows([self.row(other, self.course_key)]))
        mock_ace.send.side_effect = [Exception('SMTP down'), None]
        send_verified_upgrade_emails([
            build_payload(self.learner_email, DEFAULT_HERO_IMAGE_URL),
            build_payload(other_email, DEFAULT_HERO_IMAGE_URL),
        ])
        assert mock_ace.send.call_count == 2
        assert mock_ace.send.call_args[0][0].recipient.email_address == 'bilal@x.com'
        mock_log.exception.assert_called_once_with(
            'Failed to send verified upgrade email to user {}'.format(self.user.id)
        )
