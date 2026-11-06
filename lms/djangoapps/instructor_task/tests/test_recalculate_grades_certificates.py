# -*- coding: utf-8 -*-
"""
Tests that recalculating grades from the instructor dashboard queues certificate
generation for passing, eligible learners.
"""
from django.conf import settings
from mock import Mock, patch
from six import text_type

from course_modes.models import CourseMode
from lms.djangoapps.certificates.models import CertificateStatuses
from lms.djangoapps.certificates.tests.factories import (
    CertificateInvalidationFactory,
    CertificateWhitelistFactory,
    GeneratedCertificateFactory
)
from lms.djangoapps.instructor_task.tasks_helper.grades import _CertificateQueuer, recalculate_grades_for_course
from student.models import CourseEnrollment
from student.tests.factories import CourseEnrollmentFactory, UserFactory
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory

APPLY_ASYNC = 'lms.djangoapps.certificates.tasks.generate_certificate.apply_async'
GRADES_MODULE = 'lms.djangoapps.instructor_task.tasks_helper.grades'
PASSING = Mock(passed=True)
FAILING = Mock(passed=False)


class CertificateQueuerTestCase(ModuleStoreTestCase):
    """
    Unit tests for _CertificateQueuer eligibility checks.
    """
    def setUp(self):
        super(CertificateQueuerTestCase, self).setUp()
        self.course = CourseFactory.create()
        patcher = patch(APPLY_ASYNC)
        self.apply_async = patcher.start()
        self.addCleanup(patcher.stop)

    def _queuer(self, course=None):
        course = course or self.course
        return _CertificateQueuer(course, CourseEnrollment.objects.filter(course_id=course.id, is_active=True))

    def _enroll(self, mode=CourseMode.VERIFIED):
        user = UserFactory.create()
        CourseEnrollmentFactory.create(user=user, course_id=self.course.id, mode=mode)
        return user

    def test_passing_learner_is_queued(self):
        user = self._enroll()
        queuer = self._queuer()
        self.assertTrue(queuer.queue_if_eligible(user, PASSING))
        self.apply_async.assert_called_once_with(
            kwargs={'student': text_type(user.id), 'course_key': text_type(self.course.id)}
        )
        self.assertEqual(queuer.queued, 1)

    def test_failing_learner_is_not_queued(self):
        user = self._enroll()
        self.assertFalse(self._queuer().queue_if_eligible(user, FAILING))
        self.assertFalse(self.apply_async.called)

    def test_whitelisted_failing_learner_is_queued(self):
        user = self._enroll()
        CertificateWhitelistFactory.create(user=user, course_id=self.course.id)
        self.assertTrue(self._queuer().queue_if_eligible(user, FAILING))

    def test_honor_learner_is_queued(self):
        user = self._enroll(mode=CourseMode.HONOR)
        self.assertTrue(self._queuer().queue_if_eligible(user, PASSING))

    def test_audit_learner_is_queued_only_when_whitelisted(self):
        user = self._enroll(mode=CourseMode.AUDIT)
        self.assertFalse(self._queuer().queue_if_eligible(user, PASSING))
        CertificateWhitelistFactory.create(user=user, course_id=self.course.id)
        self.assertTrue(self._queuer().queue_if_eligible(user, PASSING))

    def test_inactive_enrollment_is_not_queued(self):
        user = self._enroll()
        CourseEnrollment.unenroll(user, self.course.id)
        self.assertFalse(self._queuer().queue_if_eligible(user, PASSING))

    def test_invalidated_certificate_is_not_queued(self):
        user = self._enroll()
        cert = GeneratedCertificateFactory.create(
            user=user, course_id=self.course.id, status=CertificateStatuses.unavailable
        )
        CertificateInvalidationFactory.create(generated_certificate=cert, invalidated_by=UserFactory.create())
        self.assertFalse(self._queuer().queue_if_eligible(user, PASSING))

    def test_existing_certificate_statuses(self):
        for status, expected in (
                (CertificateStatuses.downloadable, False),
                (CertificateStatuses.generating, False),
                (CertificateStatuses.notpassing, True),
                (CertificateStatuses.error, True),
                (CertificateStatuses.unverified, True),
        ):
            user = self._enroll()
            GeneratedCertificateFactory.create(user=user, course_id=self.course.id, status=status)
            self.assertEqual(self._queuer().queue_if_eligible(user, PASSING), expected, status)

    @patch.dict(settings.FEATURES, {'CERTIFICATES_HTML_VIEW': True})
    def test_web_certificate_needs_active_configuration(self):
        no_config = CourseFactory.create(cert_html_view_enabled=True)
        with_config = CourseFactory.create(
            cert_html_view_enabled=True, certificates={'certificates': [{'id': 1, 'is_active': True}]}
        )
        for course, expected in ((no_config, False), (with_config, True)):
            user = UserFactory.create()
            CourseEnrollmentFactory.create(user=user, course_id=course.id, mode=CourseMode.VERIFIED)
            self.assertEqual(self._queuer(course).queue_if_eligible(user, PASSING), expected)


class RecalculateGradesCertificatesTestCase(ModuleStoreTestCase):
    """
    Tests recalculate_grades_for_course queues certificates on both of its paths.
    """
    def setUp(self):
        super(RecalculateGradesCertificatesTestCase, self).setUp()
        self.course = CourseFactory.create()
        self.passing_user = UserFactory.create()
        self.failing_user = UserFactory.create()
        for user in (self.passing_user, self.failing_user):
            CourseEnrollmentFactory.create(user=user, course_id=self.course.id, mode=CourseMode.VERIFIED)
        patcher = patch(APPLY_ASYNC)
        self.apply_async = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task')
        patcher.start()
        self.addCleanup(patcher.stop)

    def _queued_user_ids(self):
        return sorted(call[1]['kwargs']['student'] for call in self.apply_async.call_args_list)

    def _run(self, **task_input):
        return recalculate_grades_for_course(None, None, self.course.id, task_input, 'recalculated')

    def test_whole_course_queues_only_passing_learners(self):
        grades = [(self.passing_user, PASSING, None), (self.failing_user, FAILING, None)]
        with patch(GRADES_MODULE + '.CourseGradeFactory') as factory:
            factory.return_value.iter.return_value = grades
            result = self._run()
        self.assertEqual(self._queued_user_ids(), [text_type(self.passing_user.id)])
        self.assertEqual(result['succeeded'], 2)

    def test_errored_grade_is_not_queued(self):
        with patch(GRADES_MODULE + '.CourseGradeFactory') as factory:
            factory.return_value.iter.return_value = [(self.passing_user, PASSING, Exception('boom'))]
            result = self._run()
        self.assertFalse(self.apply_async.called)
        self.assertEqual(result['failed'], 1)

    def test_problem_scoped_path_queues_passing_learners(self):
        grades = {self.passing_user: PASSING, self.failing_user: FAILING}
        problem = self.course.id.make_usage_key('problem', 'p1')
        with patch(GRADES_MODULE + '.CourseGradeFactory') as factory, \
                patch('lms.djangoapps.grades.tasks._update_subsection_grades') as update_subsections:
            factory.return_value.update.side_effect = lambda user, course: grades[user]
            result = self._run(problem_location=text_type(problem))
        self.assertEqual(update_subsections.call_count, 2)
        self.assertEqual(self._queued_user_ids(), [text_type(self.passing_user.id)])
        self.assertEqual(result['succeeded'], 2)

    def test_single_student_only_touches_that_student(self):
        with patch(GRADES_MODULE + '.CourseGradeFactory') as factory:
            factory.return_value.iter.return_value = [(self.passing_user, PASSING, None)]
            self._run(student_id=self.passing_user.id)
        self.assertEqual(self._queued_user_ids(), [text_type(self.passing_user.id)])

    def test_frozen_grades_queue_nothing(self):
        with patch(GRADES_MODULE + '.are_grades_frozen', return_value=True), \
                patch(GRADES_MODULE + '.CourseGradeFactory') as factory:
            result = self._run()
        self.assertFalse(factory.called)
        self.assertFalse(self.apply_async.called)
        self.assertEqual(result['skipped'], 2)
