"""Tests for the award_program_certificate_for_courses management command."""


import mock
from django.conf import settings
from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils import timezone
from opaque_keys.edx.keys import CourseKey

from lms.djangoapps.certificates.api import MODES
from lms.djangoapps.certificates.models import CertificateStatuses
from lms.djangoapps.certificates.tests.factories import GeneratedCertificateFactory
from openedx.core.djangoapps.catalog.tests.factories import (
    CourseFactory,
    CourseRunFactory,
    ProgramFactory,
    generate_course_run_key,
)
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.core.djangoapps.credentials.tests.mixins import CredentialsApiConfigMixin
from openedx.core.djangolib.testing.utils import skip_unless_lms
from student.tests.factories import UserFactory

COMMAND = 'award_program_certificate_for_courses'
COMMAND_MODULE = (
    'openedx.features.pakx.lms.pakx_admin_app.management.commands.award_program_certificate_for_courses'
)


@mock.patch(COMMAND_MODULE + '.get_programs')
@mock.patch(COMMAND_MODULE + '.get_completed_programs')
@mock.patch(COMMAND_MODULE + '.get_certified_programs')
@mock.patch(COMMAND_MODULE + '.send_program_certificate_email')
@mock.patch(COMMAND_MODULE + '.get_credentials_api_client')
@skip_unless_lms
class AwardProgramCertificateForCoursesTests(CredentialsApiConfigMixin, TestCase):
    """
    Tests for awarding a program certificate against an explicit set of course runs.

    The credentials client is mocked, so award_program_certificate runs for real
    against a MagicMock client and we assert on ``client.credentials.post``.
    """

    program_uuid = '3e7b5b8a-1411-4b5e-a9d8-de88a03ef940'
    course_run_key, alternate_course_run_key = (generate_course_run_key() for __ in range(2))

    def setUp(self):
        super(AwardProgramCertificateForCoursesTests, self).setUp()

        self.alice = UserFactory()
        self.bob = UserFactory()
        # Service user resolved before the (mocked) credentials client is built.
        UserFactory(username=settings.CREDENTIALS_SERVICE_USERNAME)

        CourseOverviewFactory(id=CourseKey.from_string(self.course_run_key))
        CourseOverviewFactory(id=CourseKey.from_string(self.alternate_course_run_key))

        # Keep issuance disabled while seeding certificates so the save signals
        # don't fire real award tasks; re-enable per test before invoking the command.
        self.create_credentials_config(enable_learner_issuance=False)

    # -- helpers ----------------------------------------------------------------

    def _program(self):
        """A program configured with both course runs."""
        return ProgramFactory(
            uuid=self.program_uuid,
            courses=[
                CourseFactory(course_runs=[CourseRunFactory(key=self.course_run_key)]),
                CourseFactory(course_runs=[CourseRunFactory(key=self.alternate_course_run_key)]),
            ],
        )

    def _cert(self, user, course_run_key):
        GeneratedCertificateFactory(
            user=user,
            course_id=course_run_key,
            mode=MODES.verified,
            status=CertificateStatuses.downloadable,
        )

    def _call(self, **kwargs):
        kwargs.setdefault('program_uuid', self.program_uuid)
        kwargs.setdefault('course_ids', [self.course_run_key, self.alternate_course_run_key])
        call_command(COMMAND, **kwargs)

    def _post(self, mock_client):
        """The mocked credentials POST endpoint."""
        return mock_client.return_value.credentials.post

    # -- tests ------------------------------------------------------------------

    def test_override_award_and_email(
        self, mock_client, mock_email, mock_certified, mock_completed, mock_programs,
    ):
        """A learner passing all supplied course-ids is awarded and emailed."""
        mock_programs.return_value = self._program()
        mock_completed.return_value = {}       # meter finds nothing
        mock_certified.return_value = []        # no existing program cert

        self._cert(self.alice, self.course_run_key)
        self._cert(self.alice, self.alternate_course_run_key)
        self._cert(self.bob, self.course_run_key)   # only one -> not override-eligible

        self.create_credentials_config(enable_learner_issuance=True)
        self._call(commit=True)

        self._post(mock_client).assert_called_once()
        self.assertEqual(mock_email.call_count, 1)
        emailed_user = mock_email.call_args[0][0]
        self.assertEqual(emailed_user.username, self.alice.username)

    def test_dry_run_awards_nothing(
        self, mock_client, mock_email, mock_certified, mock_completed, mock_programs,
    ):
        """Without --commit the command neither builds a client nor emails."""
        mock_programs.return_value = self._program()
        mock_completed.return_value = {}
        mock_certified.return_value = []

        self._cert(self.alice, self.course_run_key)
        self._cert(self.alice, self.alternate_course_run_key)

        self.create_credentials_config(enable_learner_issuance=True)
        self._call(commit=False)

        mock_client.assert_not_called()
        mock_email.assert_not_called()

    def test_meter_fallback_award(
        self, mock_client, mock_email, mock_certified, mock_completed, mock_programs,
    ):
        """A learner the standard meter marks complete is awarded even without override certs."""
        mock_programs.return_value = self._program()
        mock_certified.return_value = []
        # Meter reports bob complete; alice absent so she never qualifies.
        mock_completed.return_value = {self.program_uuid: timezone.now()}

        # bob is in the candidate pool via one program-configured run, but does not
        # hold both override course-ids.
        self._cert(self.bob, self.course_run_key)

        self.create_credentials_config(enable_learner_issuance=True)
        self._call(commit=True)

        self._post(mock_client).assert_called_once()
        self.assertEqual(mock_email.call_args[0][0].username, self.bob.username)

    def test_skip_already_certified(
        self, mock_client, mock_email, mock_certified, mock_completed, mock_programs,
    ):
        """A learner already holding this program certificate is skipped (no post, no email)."""
        mock_programs.return_value = self._program()
        mock_completed.return_value = {}
        mock_certified.return_value = [self.program_uuid]   # already awarded

        self._cert(self.alice, self.course_run_key)
        self._cert(self.alice, self.alternate_course_run_key)

        self.create_credentials_config(enable_learner_issuance=True)
        self._call(commit=True)

        self._post(mock_client).assert_not_called()
        mock_email.assert_not_called()

    def test_usernames_restrict(
        self, mock_client, mock_email, mock_certified, mock_completed, mock_programs,
    ):
        """--usernames limits evaluation to the named learners."""
        mock_programs.return_value = self._program()
        mock_completed.return_value = {}
        mock_certified.return_value = []

        for user in (self.alice, self.bob):
            self._cert(user, self.course_run_key)
            self._cert(user, self.alternate_course_run_key)

        self.create_credentials_config(enable_learner_issuance=True)
        self._call(commit=True, usernames=[self.alice.username])

        self._post(mock_client).assert_called_once()
        self.assertEqual(mock_email.call_args[0][0].username, self.alice.username)

    def test_program_not_found_raises(
        self, mock_client, mock_email, mock_certified, mock_completed, mock_programs,
    ):
        """An unknown program uuid aborts with a CommandError."""
        mock_programs.return_value = None
        self.create_credentials_config(enable_learner_issuance=True)

        with self.assertRaises(CommandError):
            self._call(commit=True)
        self._post(mock_client).assert_not_called()

    def test_issuance_disabled_raises(
        self, mock_client, mock_email, mock_certified, mock_completed, mock_programs,
    ):
        """The command refuses to run while credentials issuance is disabled."""
        mock_programs.return_value = self._program()
        # setUp left issuance disabled; do not re-enable.

        with self.assertRaises(CommandError):
            self._call(commit=True)
        self._post(mock_client).assert_not_called()
