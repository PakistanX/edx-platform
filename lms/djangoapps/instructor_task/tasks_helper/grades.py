"""
Functionality for generating grade reports.
"""

import copy
import csv
import io
import json
import logging
import re
from collections import OrderedDict, defaultdict
from datetime import datetime
from itertools import chain
from tempfile import TemporaryFile
from time import time
from uuid import uuid4

import six
from celery.states import FAILURE, SUCCESS
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from lazy import lazy
from opaque_keys.edx.keys import UsageKey
from pytz import UTC
from six import text_type
from six.moves import zip, zip_longest

from course_blocks.api import get_course_blocks
from course_modes.models import CourseMode
from lms.djangoapps.certificates.models import CertificateWhitelist, GeneratedCertificate, certificate_info_for_user
from lms.djangoapps.courseware.courses import get_course_by_id
from lms.djangoapps.courseware.user_state_client import DjangoXBlockUserStateClient
from lms.djangoapps.grades.api import CourseGradeFactory
from lms.djangoapps.grades.api import context as grades_context
from lms.djangoapps.grades.api import prefetch_course_and_subsection_grades
from lms.djangoapps.grades.grade_utils import are_grades_frozen
from lms.djangoapps.instructor_analytics.basic import list_problem_responses
from lms.djangoapps.instructor_analytics.csvs import format_dictlist
from lms.djangoapps.instructor_task.config.waffle import (
    generate_grade_report_for_verified_only,
    optimize_get_learners_switch_enabled
)
from lms.djangoapps.teams.models import CourseTeamMembership
from lms.djangoapps.verify_student.services import IDVerificationService
from openedx.core.djangoapps.content.block_structure.api import get_course_in_cache
from openedx.core.djangoapps.course_groups.cohorts import bulk_cache_cohorts, get_cohort, is_course_cohorted
from openedx.core.djangoapps.user_api.course_tag.api import BulkCourseTags
from openedx.core.lib.cache_utils import get_cache
from completion.models import BlockCompletion
from openedx.features.course_experience.utils import get_course_outline_block_tree
from openedx.features.pakx.lms.overrides.models import CourseProgressStats
from openedx.features.pakx.lms.overrides.utils import (
    CORE_BLOCK_TYPES,
    PROBLEM_BLOCK_TYPES,
    VIDEO_BLOCK_TYPES,
    create_dummy_request,
    get_progress_statistics_by_block_types,
)
from lms.djangoapps.instructor_task.config.waffle import (
    default_progress_structure_mode,
    parallelize_grade_report_enabled,
)
from lms.djangoapps.instructor_task.models import PROGRESS, InstructorTask, ReportStore
from student.models import CourseEnrollment
from student.roles import BulkRoleCache
from xmodule.modulestore.django import modulestore
from xmodule.partitions.partitions_service import PartitionService
from xmodule.split_test_module import get_split_user_partitions

from .runner import TaskProgress
from .utils import upload_csv_to_report_store, upload_file_to_report_store

TASK_LOG = logging.getLogger('edx.celery.task')

# Emit a heartbeat progress log line roughly every this many learners while a
# (sequential) grade report streams, so operators can confirm from the worker
# logs that the task is advancing even when the dashboard progress column is
# unavailable.
GRADE_REPORT_LOG_PROGRESS_EVERY = 50


def grade_report_enrolled_count(course_id):
    """
    Number of learners a grade report processes for ``course_id`` before any
    row-range slice. Single source of truth shared by the progress-bar
    denominator, the instructor-dashboard hint, and the batch-range validation,
    so those three never drift.
    """
    return CourseEnrollment.objects.users_enrolled_in(
        course_id,
        include_inactive=True,
        verified_only=generate_grade_report_for_verified_only(),
    ).count()

# Block types that do not count toward completion (mirrors the "completable"
# filter used by get_course_outline_block_tree's recurse_mark_complete). Used by
# the uniform (A) progress-column path when overlaying completion in memory.
COMPLETION_EXCLUDED_BLOCK_TYPES = frozenset([
    'discussion', 'pakx_microlearning', 'pakx_completion',
    'google_drive', 'google-drive', 'google_document', 'google-document',
])


def recalculate_grades_for_course(_xmodule_instance_args, _entry_id, course_id, task_input, action_name):
    """
    Recompute course and subsection grades for enrolled learners, tracking
    progress through the instructor_task framework.

    ``task_input`` may contain:
        student_id (optional): recompute for just this user id; otherwise all
            active enrollments in the course are recomputed.
        force (optional): if truthy, recompute even when the course's grades
            are frozen. Only ever set from the Django admin by a superuser.
    """
    start_time = time()
    student_id = task_input.get('student_id')
    force = task_input.get('force', False)
    problem_location = task_input.get('problem_location')

    # If a problem location is given, recompute only the subsection(s) that
    # contain that problem; otherwise recompute the whole course grade.
    scored_block_key = None
    if problem_location:
        scored_block_key = UsageKey.from_string(problem_location).map_into_course(course_id)

    enrollments = CourseEnrollment.objects.filter(course_id=course_id, is_active=True)
    if student_id:
        enrollments = enrollments.filter(user_id=student_id)

    total = enrollments.count()
    task_progress = TaskProgress(action_name, total, start_time)
    task_progress.update_task_state()

    # When grades are frozen we skip the actual recompute unless forced.
    if are_grades_frozen(course_id) and not force:
        task_progress.attempted = total
        task_progress.skipped = total
        return task_progress.update_task_state()

    if scored_block_key is not None:
        # Subsection-scoped path: reuse the event-path helper that recomputes
        # every subsection containing the given problem.
        # Imported here to avoid a heavy import at module load time.
        from lms.djangoapps.grades.tasks import _update_subsection_grades
        for enrollment in enrollments:
            task_progress.attempted += 1
            try:
                _update_subsection_grades(
                    course_id,
                    scored_block_key,
                    only_if_higher=None,
                    user_id=enrollment.user_id,
                    score_deleted=False,
                    force_update_subsections=True,
                )
                task_progress.succeeded += 1
            except Exception:  # pylint: disable=broad-except
                TASK_LOG.exception(
                    u'Failed to recalculate subsection grades for user %s in course %s',
                    enrollment.user_id,
                    course_id,
                )
                task_progress.failed += 1
            task_progress.update_task_state()
    else:
        # Whole-course path: reuse CourseGradeFactory().iter, which pre-fetches
        # the collected course structure once and force-updates each learner's
        # course grade (the same call used by the grade report and the
        # compute_grades management command).
        users = (enrollment.user for enrollment in enrollments)
        for _student, _course_grade, error in CourseGradeFactory().iter(
                users=users, course_key=course_id, force_update=True,
        ):
            task_progress.attempted += 1
            if error:
                task_progress.failed += 1
            else:
                task_progress.succeeded += 1
            task_progress.update_task_state()

    return task_progress.update_task_state()


ENROLLED_IN_COURSE = 'enrolled'

NOT_ENROLLED_IN_COURSE = 'unenrolled'


def _user_enrollment_status(user, course_id):
    """
    Returns the enrollment activation status in the given course
    for the given user.
    """
    enrollment_is_active = CourseEnrollment.enrollment_mode_for_user(user, course_id)[1]
    if enrollment_is_active:
        return ENROLLED_IN_COURSE
    return NOT_ENROLLED_IN_COURSE


def _flatten(iterable):
    return list(chain.from_iterable(iterable))


class GradeReportBase(object):
    """
    Base class for grade reports (ProblemGradeReport and CourseGradeReport).
    """

    def _get_enrolled_learner_count(self, context):
        """
        Returns count of number of learner enrolled in course.
        """
        return CourseEnrollment.objects.users_enrolled_in(
            course_id=context.course_id,
            include_inactive=True,
            verified_only=context.report_for_verified_only,
        ).count()

    def log_task_info(self, context, message):
        """
        Updates the status on the celery task to the given message.
        Also logs the update.
        """
        fmt = u'Task: {task_id}, InstructorTask ID: {entry_id}, Course: {course_id}, Input: {task_input}'
        task_info_string = fmt.format(
            task_id=context.task_id,
            entry_id=context.entry_id,
            course_id=context.course_id,
            task_input=context.task_input
        )
        TASK_LOG.info(u'%s, Task type: %s, %s, %s', task_info_string, context.action_name,
                      message, context.task_progress.state)

    def _handle_empty_generator(self, generator, default):
        """
        Handle empty generator.
        Return default if the generator is emtpy, otherwise return all
        its iterations (including the first which was used for validation).
        """
        TASK_LOG.info('GradeReport: Checking generator')
        empty_generator_sentinel = object()
        first_iteration_output = next(generator, empty_generator_sentinel)
        generator_is_empty = first_iteration_output == empty_generator_sentinel

        if generator_is_empty:
            TASK_LOG.info('GradeReport: Generator is empty')
            yield default

        else:
            TASK_LOG.info('GradeReport: Generator is not empty')
            yield first_iteration_output
            for element in generator:
                yield element

    def _batch_users(self, context):
        """
        Returns a generator of batches of users.
        """
        def grouper(iterable, chunk_size=100, fillvalue=None):
            args = [iter(iterable)] * chunk_size
            return zip_longest(*args, fillvalue=fillvalue)

        def get_enrolled_learners_for_course(course_id, verified_only=False):
            """
            Get all the enrolled users in a course chunk by chunk.
            This generator method fetches & loads the enrolled user objects on demand which in chunk
            size defined. This method is a workaround to avoid out-of-memory errors.
            """
            self.log_additional_info_for_testing(
                context,
                'ProblemGradeReport: Starting batching of enrolled students'
            )

            filter_kwargs = {
                'courseenrollment__course_id': course_id,
            }
            if verified_only:
                filter_kwargs['courseenrollment__mode'] = CourseMode.VERIFIED

            user_ids_list = get_user_model().objects.filter(**filter_kwargs).values_list('id', flat=True).order_by('id')
            user_chunks = grouper(user_ids_list)
            for user_ids in user_chunks:
                user_ids = [user_id for user_id in user_ids if user_id is not None]
                min_id = min(user_ids)
                max_id = max(user_ids)
                users = get_user_model().objects.filter(
                    id__gte=min_id,
                    id__lte=max_id,
                    **filter_kwargs
                ).select_related('profile')

                self.log_additional_info_for_testing(context, 'ProblemGradeReport: user chunk yielded successfully')
                yield users

        course_id = context.course_id
        return get_enrolled_learners_for_course(course_id=course_id, verified_only=context.report_for_verified_only)

    def _compile(self, context, batched_rows):
        """
        Compiles and returns the complete list of (success_rows, error_rows) for
        the given batched_rows and context.
        """
        # partition and chain successes and errors
        success_rows, error_rows = zip(*batched_rows)
        success_rows = list(chain(*success_rows))
        error_rows = list(chain(*error_rows))

        # update metrics on task status
        context.task_progress.succeeded = len(success_rows)
        context.task_progress.failed = len(error_rows)
        context.task_progress.attempted = context.task_progress.succeeded + context.task_progress.failed
        context.task_progress.total = context.task_progress.attempted
        return success_rows, error_rows

    def _upload(self, context, success_rows, error_rows):
        """
        Creates and uploads a CSV for the given headers and rows.
        """
        date = datetime.now(UTC)
        upload_csv_to_report_store(success_rows, context.file_name, context.course_id, date)
        if len(error_rows) > 1:
            upload_csv_to_report_store(error_rows, context.file_name + '_err', context.course_id, date)

    def log_additional_info_for_testing(self, context, message):
        """
        Investigation logs for test problem grade report.

        TODO -- Remove as a part of PROD-1287
        """
        context.update_status(message)


class _CourseGradeReportContext(object):
    """
    Internal class that provides a common context to use for a single grade
    report.  When a report is parallelized across multiple processes,
    elements of this context are serialized and parsed across process
    boundaries.
    """

    def __init__(self, _xmodule_instance_args, _entry_id, course_id, _task_input, action_name):
        self.task_info_string = (
            u'Task: {task_id}, '
            u'InstructorTask ID: {entry_id}, '
            u'Course: {course_id}, '
            u'Input: {task_input}'
        ).format(
            task_id=_xmodule_instance_args.get('task_id') if _xmodule_instance_args is not None else None,
            entry_id=_entry_id,
            course_id=course_id,
            task_input=_task_input,
        )
        self.action_name = action_name
        self.course_id = course_id
        self.entry_id = _entry_id
        self.task_progress = TaskProgress(self.action_name, total=None, start_time=time())

        # Per-report options (task_input), falling back to the waffle defaults.
        #  - include_progress_columns: include the resource-intensive custom
        #    columns (live Course Progress / block types / completed & incomplete
        #    units). When off, the report is fast and still carries a single
        #    Course Progress column read from the stored CourseProgressStats
        #    value (as of the last progress sync) instead of computing it.
        #  - progress_structure_mode: how those columns are computed --
        #    'legacy'      : original per-learner path (two page-render helpers),
        #    'per_learner' : (B) per-learner visibility, lightweight tree,
        #    'uniform'     : (A) one shared structure + bulk completion (fastest;
        #                    only correct when the course is not gated per learner).
        task_input = _task_input or {}
        self.include_progress_columns = bool(task_input.get('include_progress_columns', True))
        self.progress_structure_mode = (
            task_input.get('progress_structure_mode') or default_progress_structure_mode()
        )
        # Advanced batch controls (superuser-only; validated in the view). A
        # custom per-batch size, and a half-open [batch_start, batch_end) range
        # of 0-based rows into the id-ordered enrolled-learner list. None means
        # "use the default batch size / process every learner".
        self.user_batch_size = task_input.get('user_batch_size')
        self.batch_start = task_input.get('batch_start')
        self.batch_end = task_input.get('batch_end')

    @lazy
    def course(self):
        return get_course_by_id(self.course_id)

    @lazy
    def course_structure(self):
        return get_course_in_cache(self.course_id)

    @lazy
    def course_experiments(self):
        return get_split_user_partitions(self.course.user_partitions)

    @lazy
    def teams_enabled(self):
        return self.course.teams_enabled

    @lazy
    def cohorts_enabled(self):
        return is_course_cohorted(self.course_id)

    @lazy
    def graded_assignments(self):
        """
        Returns an OrderedDict that maps an assignment type to a dict of
        subsection-headers and average-header.
        """
        grading_cxt = grades_context.grading_context(self.course, self.course_structure)
        graded_assignments_map = OrderedDict()
        for assignment_type_name, subsection_infos in six.iteritems(grading_cxt['all_graded_subsections_by_type']):
            graded_subsections_map = OrderedDict()
            for subsection_index, subsection_info in enumerate(subsection_infos, start=1):
                subsection = subsection_info['subsection_block']
                header_name = u"{assignment_type} {subsection_index}: {subsection_name}".format(
                    assignment_type=assignment_type_name,
                    subsection_index=subsection_index,
                    subsection_name=subsection.display_name,
                )
                graded_subsections_map[subsection.location] = header_name

            average_header = u"{assignment_type}".format(assignment_type=assignment_type_name)

            # Use separate subsection and average columns only if
            # there's more than one subsection.
            separate_subsection_avg_headers = len(subsection_infos) > 1
            if separate_subsection_avg_headers:
                average_header += u" (Avg)"

            graded_assignments_map[assignment_type_name] = {
                'subsection_headers': graded_subsections_map,
                'average_header': average_header,
                'separate_subsection_avg_headers': separate_subsection_avg_headers,
                'grader': grading_cxt['subsection_type_graders'].get(assignment_type_name),
            }
        return graded_assignments_map

    def update_status(self, message):
        """
        Updates the status on the celery task to the given message.
        Also logs the update.
        """
        TASK_LOG.info(u'%s, Task type: %s, %s', self.task_info_string, self.action_name, message)
        return self.task_progress.update_task_state(extra_meta={'step': message})


class _ProblemGradeReportContext(object):
    """
    Internal class that provides a common context to use for a single problem
    grade report.  When a report is parallelized across multiple processes,
    elements of this context are serialized and parsed across process
    boundaries.
    """

    def __init__(self, _xmodule_instance_args, _entry_id, course_id, _task_input, action_name):
        task_id = _xmodule_instance_args.get('task_id') if _xmodule_instance_args is not None else None
        self.task_info_string = (
            'Task: {task_id}, '
            'InstructorTask ID: {entry_id}, '
            'Course: {course_id}, '
            'Input: {task_input}'
        ).format(
            task_id=task_id,
            entry_id=_entry_id,
            course_id=course_id,
            task_input=_task_input,
        )
        self.task_id = task_id
        self.entry_id = _entry_id
        self.task_input = _task_input
        self.action_name = action_name
        self.course_id = course_id
        self.report_for_verified_only = generate_grade_report_for_verified_only()
        self.task_progress = TaskProgress(self.action_name, total=None, start_time=time())
        self.file_name = 'problem_grade_report'

    @lazy
    def course(self):
        return get_course_by_id(self.course_id)

    @lazy
    def graded_scorable_blocks_header(self):
        """
        Returns an OrderedDict that maps a scorable block's id to its
        headers in the final report.
        """
        scorable_blocks_map = OrderedDict()
        grading_context = grades_context.grading_context_for_course(self.course)
        for assignment_type_name, subsection_infos in six.iteritems(grading_context['all_graded_subsections_by_type']):
            for subsection_index, subsection_info in enumerate(subsection_infos, start=1):
                for scorable_block in subsection_info['scored_descendants']:
                    header_name = (
                        "{assignment_type} {subsection_index}: "
                        "{subsection_name} - {scorable_block_name}"
                    ).format(
                        scorable_block_name=scorable_block.display_name,
                        assignment_type=assignment_type_name,
                        subsection_index=subsection_index,
                        subsection_name=subsection_info['subsection_block'].display_name,
                    )
                    scorable_blocks_map[scorable_block.location] = [header_name + " (Earned)",
                                                                    header_name + " (Possible)"]
        return scorable_blocks_map

    @lazy
    def course_structure(self):
        return get_course_in_cache(self.course_id)

    def update_status(self, message):
        """
        Updates the status on the celery task to the given message.
        Also logs the update.
        """
        TASK_LOG.info('%s, Task type: %s, %s', self.task_info_string, self.action_name, message)
        return self.task_progress.update_task_state(extra_meta={'step': message})


class _CertificateBulkContext(object):
    def __init__(self, context, users):
        certificate_whitelist = CertificateWhitelist.objects.filter(course_id=context.course_id, whitelist=True)
        self.whitelisted_user_ids = [entry.user_id for entry in certificate_whitelist]
        self.certificates_by_user = {
            certificate.user.id: certificate
            for certificate in
            GeneratedCertificate.objects.filter(course_id=context.course_id, user__in=users)
        }


class _TeamBulkContext(object):
    def __init__(self, context, users):
        self.enabled = context.teams_enabled
        if self.enabled:
            self.teams_by_user = {
                membership.user.id: membership.team.name
                for membership in
                CourseTeamMembership.objects.filter(team__course_id=context.course_id, user__in=users)
            }
        else:
            self.teams_by_user = {}


class _EnrollmentBulkContext(object):
    def __init__(self, context, users):
        CourseEnrollment.bulk_fetch_enrollment_states(users, context.course_id)
        self.verified_users = set(IDVerificationService.get_verified_user_ids(users))


class _CourseGradeBulkContext(object):
    def __init__(self, context, users):
        self.certs = _CertificateBulkContext(context, users)
        self.teams = _TeamBulkContext(context, users)
        self.enrollments = _EnrollmentBulkContext(context, users)
        bulk_cache_cohorts(context.course_id, users)
        BulkRoleCache.prefetch(users)
        prefetch_course_and_subsection_grades(context.course_id, users)
        BulkCourseTags.prefetch(context.course_id, users)


class CourseGradeReport(object):
    """
    Class to encapsulate functionality related to generating Grade Reports.
    """
    # Batch size for chunking the list of enrollees in the course.
    USER_BATCH_SIZE = 100

    @classmethod
    def generate(cls, _xmodule_instance_args, _entry_id, course_id, _task_input, action_name):
        """
        Public method to generate a grade report.
        """
        with modulestore().bulk_operations(course_id):
            context = _CourseGradeReportContext(_xmodule_instance_args, _entry_id, course_id, _task_input, action_name)
            return CourseGradeReport()._generate(context)

    # Number of learners each parallel chunk processes when the report is fanned
    # out across subtasks (see _generate_parallel).
    PARALLEL_CHUNK_SIZE = 500

    def _generate(self, context):
        """
        Internal method for generating a grade report for the given context.

        Rows are streamed batch-by-batch straight to an on-disk temporary file and
        uploaded from there, so the full result set is never held in memory (which
        previously caused OOM on large courses).

        When the parallelize waffle is on, the work is instead fanned out across
        parallel subtasks (one per learner chunk), each writing a partial CSV that
        a finalize task concatenates into the single report.
        """
        if parallelize_grade_report_enabled():
            return self._generate_parallel(context)

        context.update_status(u'Starting grades')
        success_headers = self._success_headers(context)
        error_headers = self._error_headers()

        # Set the total up front so the dashboard shows a real progress bar
        # (completed of total) that advances after each batch, rather than
        # "No status information available".
        context.task_progress.total = self._total_enrolled(context)
        batched_rows = self._batched_rows(context)

        entry = InstructorTask.objects.get(pk=context.entry_id)
        TASK_LOG.info(
            u'GradeReport[seq] start: InstructorTask=%s task_id=%s course=%s enrolled=%s '
            u'mode=%s include_progress=%s',
            context.entry_id, entry.task_id, text_type(context.course_id),
            context.task_progress.total, context.progress_structure_mode,
            context.include_progress_columns,
        )

        context.update_status(u'Compiling and uploading grades')
        report_name = self._stream_and_upload(context, success_headers, error_headers, batched_rows, entry)

        TASK_LOG.info(
            u'GradeReport[seq] done: InstructorTask=%s task_id=%s course=%s succeeded=%s failed=%s '
            u'duration_ms=%s report=%s',
            context.entry_id, entry.task_id, text_type(context.course_id),
            context.task_progress.succeeded, context.task_progress.failed,
            int((time() - context.task_progress.start_time) * 1000), report_name,
        )

        # Record the report filename alongside the progress (duration_ms is
        # already part of the state) so the UI can show duration against the link.
        return context.task_progress.update_task_state(extra_meta={
            'step': u'Completed grades',
            'report_name': report_name,
        })

    def _total_enrolled(self, context):
        """
        Number of enrollees the report will process -- the denominator used for
        the progress bar. Mirrors the enrollment set iterated by ``_batch_users``,
        including the optional [batch_start, batch_end) row slice.
        """
        base = grade_report_enrolled_count(context.course_id)
        start = context.batch_start or 0
        end = context.batch_end if context.batch_end is not None else base
        return max(0, min(end, base) - start)

    def _enrolled_user_ids(self, context):
        """
        Ordered list of user ids the report will process (parallel path),
        including the optional [batch_start, batch_end) row slice.
        """
        user_ids = CourseEnrollment.objects.users_enrolled_in(
            context.course_id,
            include_inactive=True,
            verified_only=generate_grade_report_for_verified_only(),
        ).order_by('id').values_list('id', flat=True)
        # Push the [start, end) row slice to the DB (OFFSET/LIMIT) instead of
        # materializing every id. A None end means "through the last learner".
        return list(user_ids[(context.batch_start or 0):context.batch_end])

    @staticmethod
    def _partial_report_name(entry_id, part_index):
        """Storage name for a chunk's partial CSV (concatenated by finalize)."""
        return u'_grade_report_parts/{entry_id}/part_{part_index:05d}'.format(
            entry_id=entry_id, part_index=part_index,
        )

    def _generate_parallel(self, context):
        """
        Fan the report out across parallel subtasks. Each chunk of learners is
        rendered to a partial CSV by ``calculate_grades_csv_chunk``; a
        ``finalize_grades_csv`` chord callback concatenates the partials into the
        single report and marks the InstructorTask complete.

        Subtasks are registered on the InstructorTask so BaseInstructorTask.on_success
        defers completion to the finalize task (it only auto-completes when no
        subtasks are registered).
        """
        from celery import chord
        from lms.djangoapps.instructor_task.subtasks import initialize_subtask_info
        from lms.djangoapps.instructor_task.tasks import calculate_grades_csv_chunk, finalize_grades_csv

        context.update_status(u'Starting grades (parallel)')
        # A superuser-supplied batch size, when set, also drives the parallel
        # chunk size so the control is honored on this path (not just sequential).
        chunk_size = (
            context.user_batch_size
            or getattr(settings, 'GRADE_REPORT_PARALLEL_CHUNK_SIZE', self.PARALLEL_CHUNK_SIZE)
        )
        user_ids = self._enrolled_user_ids(context)
        chunks = [
            user_ids[i:i + chunk_size]
            for i in range(0, len(user_ids), chunk_size)
        ] or [[]]
        chunk_task_ids = [text_type(uuid4()) for _ in chunks]

        entry = InstructorTask.objects.get(pk=context.entry_id)
        task_progress = initialize_subtask_info(entry, context.action_name, len(user_ids), chunk_task_ids)

        routing_key = getattr(settings, 'GRADES_DOWNLOAD_ROUTING_KEY', None)

        def _error_handler():
            # Immutable so it runs with just these kwargs; marks the task FAILURE
            # and cleans up partials when a chunk fails (a failed chord header
            # otherwise leaves the InstructorTask stuck in PROGRESS forever).
            return finalize_grades_csv.subtask(
                kwargs={'entry_id': context.entry_id, 'num_parts': len(chunks), 'failed': True},
                routing_key=routing_key,
                immutable=True,
            )

        header_tasks = [
            calculate_grades_csv_chunk.subtask(
                kwargs={
                    'entry_id': context.entry_id,
                    'part_index': part_index,
                    'user_ids': chunk,
                },
                task_id=chunk_task_ids[part_index],
                routing_key=routing_key,
                link_error=_error_handler(),
            )
            for part_index, chunk in enumerate(chunks)
        ]
        callback = finalize_grades_csv.subtask(
            kwargs={'entry_id': context.entry_id, 'num_parts': len(chunks)},
            routing_key=routing_key,
        )
        chord(header_tasks)(callback)
        TASK_LOG.info(
            u'GradeReport[parallel] dispatched: InstructorTask=%s parent_task_id=%s course=%s '
            u'enrolled=%s chunks=%s chunk_size=%s first_chunk_task_id=%s routing_key=%s',
            context.entry_id, entry.task_id, text_type(context.course_id),
            len(user_ids), len(chunks), chunk_size,
            (chunk_task_ids[0] if chunk_task_ids else None), routing_key,
        )
        return task_progress

    @staticmethod
    def _partial_error_name(entry_id, part_index):
        """Storage name for a chunk's partial error CSV (concatenated by finalize)."""
        return u'_grade_report_parts/{entry_id}/error_{part_index:05d}'.format(
            entry_id=entry_id, part_index=part_index,
        )

    def generate_partial(self, context, user_ids, part_index):
        """
        Render one chunk of learners to a partial CSV (no header) and store it.
        Error rows are written to a separate partial file rather than returned
        through the result backend, so only small counts cross the broker.
        """
        TASK_LOG.info(
            u'GradeReport[parallel] chunk start: InstructorTask=%s part=%s learners=%s',
            context.entry_id, part_index, len(user_ids),
        )
        users = list(get_user_model().objects.filter(id__in=user_ids).select_related('profile'))
        with modulestore().bulk_operations(context.course_id):
            success_rows, error_rows = self._rows_for_users(context, users)

        report_store = ReportStore.from_config('GRADES_DOWNLOAD')
        report_store.store_rows(
            context.course_id, self._partial_report_name(context.entry_id, part_index), success_rows,
        )
        if error_rows:
            report_store.store_rows(
                context.course_id, self._partial_error_name(context.entry_id, part_index), error_rows,
            )
        TASK_LOG.info(
            u'GradeReport[parallel] chunk done: InstructorTask=%s part=%s learners=%s '
            u'succeeded=%s failed=%s',
            context.entry_id, part_index, len(user_ids), len(success_rows), len(error_rows),
        )
        return {
            'part_index': part_index,
            'succeeded': len(success_rows),
            'failed': len(error_rows),
        }

    def _concatenate_parts(self, context, report_store, num_parts, headers, report_prefix, name_fn, date,
                           skip_if_empty=False, tracker_name=None):
        """
        Concatenate stored partial CSVs (0..num_parts-1) behind `headers`, upload,
        and return the uploaded report's filename (or None if skipped).

        ``tracker_name`` is forwarded to the upload so the analytics event name
        can stay fixed even when ``report_prefix`` (the filename) varies.
        """
        paths = []
        for part_index in range(num_parts):
            path = report_store.path_to(context.course_id, name_fn(context.entry_id, part_index))
            if report_store.storage.exists(path):
                paths.append(path)
        if skip_if_empty and not paths:
            return None
        final_file = TemporaryFile()
        try:
            header_buffer = io.StringIO()
            csv.writer(header_buffer).writerow(headers)
            final_file.write(header_buffer.getvalue().encode('utf-8'))
            for path in paths:
                with report_store.storage.open(path) as part_file:
                    for line in part_file:
                        final_file.write(line if isinstance(line, bytes) else line.encode('utf-8'))
            final_file.seek(0)
            return upload_file_to_report_store(
                final_file, report_prefix, context.course_id, date, tracker_name=tracker_name,
            )
        finally:
            final_file.close()

    def _delete_parts(self, context, report_store, num_parts):
        """Remove all partial success/error files for this report, ignoring errors."""
        for part_index in range(num_parts):
            for name_fn in (self._partial_report_name, self._partial_error_name):
                path = report_store.path_to(context.course_id, name_fn(context.entry_id, part_index))
                try:
                    if report_store.storage.exists(path):
                        report_store.storage.delete(path)
                except Exception:  # pylint: disable=broad-except
                    TASK_LOG.warning(u'Could not delete partial grade report %s', path)

    @staticmethod
    def _elapsed_ms(entry):
        """Total wall time since the report was submitted (from the stored start_time)."""
        try:
            existing = json.loads(entry.task_output) if entry.task_output else {}
            start = existing.get('start_time')
        except (ValueError, TypeError):
            start = None
        return int((time() - start) * 1000) if start else 0

    def finalize(self, context, part_results, failed=False, num_parts=None):
        """
        Concatenate the partial CSVs (in order) behind the header into the single
        report, upload it, mark the InstructorTask complete, and remove the parts.

        ``num_parts`` is used for concatenation/cleanup so the error path (where
        ``part_results`` is empty) still cleans up the partials that succeeded
        chunks wrote. If concatenation/upload itself fails, the task is marked
        FAILURE rather than left stuck in PROGRESS.
        """
        entry = InstructorTask.objects.get(pk=context.entry_id)
        report_store = ReportStore.from_config('GRADES_DOWNLOAD')

        if num_parts is None:
            num_parts = len(part_results or [])
        succeeded = sum(part.get('succeeded', 0) for part in (part_results or []))
        failed_count = sum(part.get('failed', 0) for part in (part_results or []))

        report_name = None
        error_message = None
        if not failed:
            try:
                date = datetime.now(UTC)
                report_name = self._concatenate_parts(
                    context, report_store, num_parts, self._success_headers(context),
                    self._report_csv_name(context), self._partial_report_name, date,
                    tracker_name='grade_report',
                )
                self._concatenate_parts(
                    context, report_store, num_parts, self._error_headers(),
                    'grade_report_err', self._partial_error_name, date, skip_if_empty=True,
                )
            except Exception as exc:  # pylint: disable=broad-except
                TASK_LOG.exception(u'Grade report finalize failed for InstructorTask %s', context.entry_id)
                failed = True
                error_message = text_type(exc)

        # Always attempt to remove the partial files (success or failure).
        self._delete_parts(context, report_store, num_parts)

        context.task_progress.total = succeeded + failed_count
        context.task_progress.attempted = succeeded + failed_count
        context.task_progress.succeeded = succeeded
        context.task_progress.failed = failed_count
        progress = context.task_progress.state
        progress['duration_ms'] = self._elapsed_ms(entry)
        if report_name:
            progress['report_name'] = report_name
        if failed:
            entry.task_output = InstructorTask.create_output_for_failure(
                Exception(error_message or 'grade report failed: one or more learner chunks errored'), '',
            )
            entry.task_state = FAILURE
        else:
            entry.task_output = InstructorTask.create_output_for_success(progress)
            entry.task_state = SUCCESS
        entry.save_now()
        TASK_LOG.info(
            u'GradeReport[parallel] finalize: InstructorTask=%s task_id=%s course=%s state=%s '
            u'parts=%s succeeded=%s failed=%s duration_ms=%s report=%s',
            context.entry_id, entry.task_id, text_type(context.course_id), entry.task_state,
            num_parts, succeeded, failed_count, progress.get('duration_ms'), report_name,
        )
        return progress

    @staticmethod
    def _report_csv_name(context):
        """
        Report file base name that reflects the options chosen, so downloads are
        self-describing, e.g. ``grade_report_fast``,
        ``grade_report_uniform``, ``grade_report_perlearner_rows0-1000``.
        """
        parts = ['grade_report']
        if not context.include_progress_columns:
            parts.append('fast')
        else:
            parts.append(context.progress_structure_mode.replace('_', ''))
        if context.batch_start is not None or context.batch_end is not None:
            start = context.batch_start or 0
            end = context.batch_end if context.batch_end is not None else 'end'
            parts.append(u'rows{}-{}'.format(start, end))
        return '_'.join(parts)

    @staticmethod
    def _persist_task_progress(entry, progress_state):
        """
        Best-effort persist of in-progress state to the InstructorTask row, so
        the Pending Tasks status column advances even when the Celery result
        backend does not retain custom PROGRESS meta (the AsyncResult path).
        Subtask-based reports already persist to the row this way; this brings
        the sequential path in line.

        Best-effort by design: a progress-status write failing (e.g. a transient
        lock timeout) must never abort an in-flight report, so errors are logged
        and swallowed. Only the two changed columns are written.
        """
        try:
            entry.task_state = PROGRESS
            entry.task_output = InstructorTask.create_output_for_success(progress_state)
            entry.save(update_fields=['task_state', 'task_output'])
        except Exception:  # pylint: disable=broad-except
            TASK_LOG.exception(
                u'GradeReport[seq] failed to persist progress for InstructorTask=%s', entry.id,
            )

    def _stream_and_upload(self, context, success_headers, error_headers, batched_rows, entry):
        """
        Write each batch of rows to an on-disk temporary file and upload the
        finished files to the report store without buffering the whole CSV in
        memory. This bounds peak memory to roughly a single batch regardless of
        course size.

        ``entry`` is the InstructorTask row; progress is persisted to it as the
        report streams (see ``_persist_task_progress``).
        """
        date = datetime.now(UTC)
        succeeded = 0
        failed = 0
        last_logged = 0

        success_file = TemporaryFile()
        error_file = TemporaryFile()
        success_text = io.TextIOWrapper(success_file, encoding='utf-8', newline='')
        error_text = io.TextIOWrapper(error_file, encoding='utf-8', newline='')
        try:
            success_writer = csv.writer(success_text)
            error_writer = csv.writer(error_text)
            success_writer.writerow(success_headers)
            wrote_error_header = False

            for batch_success_rows, batch_error_rows in batched_rows:
                for row in batch_success_rows:
                    success_writer.writerow(row)
                    succeeded += 1
                for row in batch_error_rows:
                    if not wrote_error_header:
                        error_writer.writerow(error_headers)
                        wrote_error_header = True
                    error_writer.writerow(row)
                    failed += 1

                context.task_progress.succeeded = succeeded
                context.task_progress.failed = failed
                context.task_progress.attempted = succeeded + failed
                progress_state = context.task_progress.update_task_state()

                processed = succeeded + failed
                if processed - last_logged >= GRADE_REPORT_LOG_PROGRESS_EVERY:
                    last_logged = processed
                    # Persist progress to the InstructorTask row (throttled to
                    # this cadence) so the Pending Tasks column reads it from the
                    # DB, independent of whether the Celery result backend retains
                    # PROGRESS meta on this deployment.
                    self._persist_task_progress(entry, progress_state)
                    TASK_LOG.info(
                        u'GradeReport[seq] progress: InstructorTask=%s processed=%s/%s '
                        u'succeeded=%s failed=%s',
                        context.entry_id, processed, context.task_progress.total,
                        succeeded, failed,
                    )

            success_text.flush()
            success_file.seek(0)
            report_name = upload_file_to_report_store(
                success_file, self._report_csv_name(context), context.course_id, date,
                tracker_name='grade_report',
            )
            if wrote_error_header:
                error_text.flush()
                error_file.seek(0)
                upload_file_to_report_store(error_file, 'grade_report_err', context.course_id, date)
            return report_name
        finally:
            # Closing the text wrapper also closes the underlying temp file.
            success_text.close()
            error_text.close()

    def _success_headers(self, context):
        """
        Returns a list of all applicable column headers for this grade report.
        """
        headers = (
            ["Student ID", "Email", "Username", "Date Joined"] +
            self._grades_header(context) +
            (['Cohort Name'] if context.cohorts_enabled else []) +
            [u'Experiment Group ({})'.format(partition.name) for partition in context.course_experiments] +
            (['Team Name'] if context.teams_enabled else []) +
            ['Enrollment Track', 'Verification Status'] +
            ['Certificate Eligible', 'Certificate Delivered', 'Certificate Type'] +
            ['Enrollment Status']
        )
        if context.include_progress_columns:
            headers += [
                'Course Progress', 'Total Block Types',
                'Total Completed Block Types', ' Completed Units', 'Incomplete Units',
            ]
        else:
            # Fast flow: a single Course Progress column read from the stored
            # CourseProgressStats value (refreshed by the progress-stats cron),
            # so no per-learner structure walk is done. Labeled '(stored)' to
            # distinguish it from the live 'Course Progress' column above.
            headers += ['Course Progress (stored)']
        return headers

    def _error_headers(self):
        """
        Returns a list of error headers for this grade report.
        """
        return ["Student ID", "Username", "Error"]

    def _batched_rows(self, context):
        """
        A generator of batches of (success_rows, error_rows) for this report.
        """
        for users in self._batch_users(context):
            users = [u for u in users if u is not None]
            yield self._rows_for_users(context, users)

    def _compile(self, context, batched_rows):
        """
        Compiles and returns the complete list of (success_rows, error_rows) for
        the given batched_rows and context.
        """
        # partition and chain successes and errors
        success_rows, error_rows = zip(*batched_rows)
        success_rows = list(chain(*success_rows))
        error_rows = list(chain(*error_rows))

        # update metrics on task status
        context.task_progress.succeeded = len(success_rows)
        context.task_progress.failed = len(error_rows)
        context.task_progress.attempted = context.task_progress.succeeded + context.task_progress.failed
        context.task_progress.total = context.task_progress.attempted
        return success_rows, error_rows

    def _upload(self, context, success_headers, success_rows, error_headers, error_rows):
        """
        Creates and uploads a CSV for the given headers and rows.
        """
        date = datetime.now(UTC)
        upload_csv_to_report_store([success_headers] + success_rows, 'grade_report', context.course_id, date)
        if len(error_rows) > 0:
            error_rows = [error_headers] + error_rows
            upload_csv_to_report_store(error_rows, 'grade_report_err', context.course_id, date)

    def _grades_header(self, context):
        """
        Returns the applicable grades-related headers for this report.
        """
        graded_assignments = context.graded_assignments
        grades_header = ["Grade"]
        for assignment_info in six.itervalues(graded_assignments):
            if assignment_info['separate_subsection_avg_headers']:
                grades_header.extend(six.itervalues(assignment_info['subsection_headers']))
            grades_header.append(assignment_info['average_header'])
        return grades_header

    def _batch_users(self, context):
        """
        Returns a generator of batches of users.

        Honors the optional advanced batch controls on ``context``: a custom
        per-batch size, and a half-open [batch_start, batch_end) slice of the
        id-ordered enrollee list.
        """
        batch_size = context.user_batch_size or self.USER_BATCH_SIZE
        range_start = context.batch_start or 0
        range_end = context.batch_end  # None => through the last learner

        def _sliced(ordered):
            """
            Apply the [range_start, range_end) row slice to an ordered sequence
            or queryset. A None range_end means "through the last row".
            """
            return ordered[range_start:range_end]

        def grouper(iterable, chunk_size=batch_size, fillvalue=None):
            args = [iter(iterable)] * chunk_size
            return zip_longest(*args, fillvalue=fillvalue)

        def get_enrolled_learners_for_course(course_id, verified_only=False):
            """
            Get enrolled learners in a course.
            Arguments:
                course_id (CourseLocator): course_id to return enrollees for.
                verified_only (boolean): is a boolean when True, returns only verified enrollees.
            """
            if optimize_get_learners_switch_enabled():
                TASK_LOG.info(u'%s, Creating Course Grade with optimization', task_log_message)
                return users_for_course_v2(course_id, verified_only=verified_only)

            TASK_LOG.info(u'%s, Creating Course Grade without optimization', task_log_message)
            return users_for_course(course_id, verified_only=verified_only)

        def users_for_course(course_id, verified_only=False):
            """
            Get all the enrolled users in a course.
            This method fetches & loads the enrolled user objects at once which may cause
            out-of-memory errors in large courses. This method will be removed when
            `OPTIMIZE_GET_LEARNERS_FOR_COURSE` waffle flag is removed.
            """
            users = CourseEnrollment.objects.users_enrolled_in(
                course_id,
                include_inactive=True,
                verified_only=verified_only,
            )
            # order_by('id') makes the row slice deterministic and matches the
            # ordering used by the parallel path and the info line in the UI.
            users = users.order_by('id').select_related('profile')
            return grouper(_sliced(users))

        def users_for_course_v2(course_id, verified_only=False):
            """
            Get all the enrolled users in a course chunk by chunk.
            This generator method fetches & loads the enrolled user objects on demand which in chunk
            size defined. This method is a workaround to avoid out-of-memory errors.
            """
            filter_kwargs = {
                'courseenrollment__course_id': course_id,
            }
            if verified_only:
                filter_kwargs['courseenrollment__mode'] = CourseMode.VERIFIED

            user_ids_list = get_user_model().objects.filter(**filter_kwargs).values_list('id', flat=True).order_by('id')
            user_chunks = grouper(_sliced(user_ids_list))
            for user_ids in user_chunks:
                user_ids = [user_id for user_id in user_ids if user_id is not None]
                min_id = min(user_ids)
                max_id = max(user_ids)
                users = get_user_model().objects.filter(
                    id__gte=min_id,
                    id__lte=max_id,
                    **filter_kwargs
                ).select_related('profile')
                yield users

        course_id = context.course_id
        task_log_message = u'{}, Task type: {}'.format(context.task_info_string, context.action_name)
        report_for_verified_only = generate_grade_report_for_verified_only()
        return get_enrolled_learners_for_course(course_id=course_id, verified_only=report_for_verified_only)

    def _user_enrollment_timestamp(self, user, course_id):
        return [CourseEnrollment.get_enrollment(user, course_id).created.strftime("%Y-%m-%d %H:%M:%S")]
                       
    def _user_grades(self, course_grade, context):
        """
        Returns a list of grade results for the given course_grade corresponding
        to the headers for this report.
        """
        grade_results = []
        for _, assignment_info in six.iteritems(context.graded_assignments):
            subsection_grades, subsection_grades_results = self._user_subsection_grades(
                course_grade,
                assignment_info['subsection_headers'],
            )
            grade_results.extend(subsection_grades_results)

            assignment_average = self._user_assignment_average(course_grade, subsection_grades, assignment_info)
            if assignment_average is not None:
                grade_results.append([assignment_average])

        return [course_grade.percent] + _flatten(grade_results)

    def _user_subsection_grades(self, course_grade, subsection_headers):
        """
        Returns a list of grade results for the given course_grade corresponding
        to the headers for this report.
        """
        subsection_grades = []
        grade_results = []
        for subsection_location in subsection_headers:
            grade_result = u'Not Attempted'
            try:
                subsection_grade = course_grade.subsection_grade(subsection_location)
                if subsection_grade.attempted_graded or subsection_grade.override:
                    grade_result = subsection_grade.percent_graded
                subsection_grades.append(subsection_grade)
            except:
                pass
            grade_results.append([grade_result])
        return subsection_grades, grade_results

    def _user_assignment_average(self, course_grade, subsection_grades, assignment_info):
        if assignment_info['separate_subsection_avg_headers']:
            if assignment_info['grader']:
                if course_grade.attempted:
                    subsection_breakdown = [
                        {'percent': subsection_grade.percent_graded}
                        for subsection_grade in subsection_grades
                    ]
                    assignment_average, _ = assignment_info['grader'].total_with_drops(subsection_breakdown)
                else:
                    assignment_average = 0.0
                return assignment_average

    def _user_cohort_group_names(self, user, context):
        """
        Returns a list of names of cohort groups in which the given user
        belongs.
        """
        cohort_group_names = []
        if context.cohorts_enabled:
            group = get_cohort(user, context.course_id, assign=False, use_cached=True)
            cohort_group_names.append(group.name if group else '')
        return cohort_group_names

    def _user_experiment_group_names(self, user, context):
        """
        Returns a list of names of course experiments in which the given user
        belongs.
        """
        experiment_group_names = []
        for partition in context.course_experiments:
            group = PartitionService(context.course_id).get_group(user, partition, assign=False)
            experiment_group_names.append(group.name if group else '')
        return experiment_group_names

    def _user_visibility_key(self, user, context):
        """
        A hashable key identifying learners who see the same block structure, so
        they can share a cached structure in the uniform path. Built only from
        read-only signals -- cohort (assign=False), enrollment mode, and
        split-test experiment groups (assign=False) -- so computing it never
        assigns a learner to a cohort or experiment group as a side effect.

        Cohort id captures content-group gating (content groups map from the
        cohort); enrollment mode captures enrollment-track gating; experiment
        group names capture split-test gating. Over-splitting (e.g. two cohorts
        mapped to the same content group) only costs an extra cache entry, never
        a wrong structure.
        """
        cohort = get_cohort(user, context.course_id, assign=False, use_cached=True)
        cohort_id = cohort.id if cohort else None
        enrollment_mode = CourseEnrollment.enrollment_mode_for_user(user, context.course_id)[0]
        return (
            cohort_id,
            enrollment_mode,
            tuple(self._user_experiment_group_names(user, context)),
        )

    def _user_team_names(self, user, bulk_teams):
        """
        Returns a list of names of teams in which the given user belongs.
        """
        team_names = []
        if bulk_teams.enabled:
            team_names = [bulk_teams.teams_by_user.get(user.id, '')]
        return team_names

    def _user_verification_mode(self, user, context, bulk_enrollments):
        """
        Returns a list of enrollment-mode and verification-status for the
        given user.
        """
        enrollment_mode = CourseEnrollment.enrollment_mode_for_user(user, context.course_id)[0]
        verification_status = IDVerificationService.verification_status_for_user(
            user,
            enrollment_mode,
            user_is_verified=user.id in bulk_enrollments.verified_users,
        )
        return [enrollment_mode, verification_status]

    def _user_certificate_info(self, user, context, course_grade, bulk_certs):
        """
        Returns the course certification information for the given user.
        """
        is_whitelisted = user.id in bulk_certs.whitelisted_user_ids
        certificate_info = certificate_info_for_user(
            user,
            context.course_id,
            course_grade.letter_grade,
            is_whitelisted,
            bulk_certs.certificates_by_user.get(user.id),
        )
        return certificate_info

    def _rows_for_users(self, context, users):
        """
        Returns a list of rows for the given users for this report.
        """

        def _flatten_course_block_tree(blocks):
            completed_units = []
            incomplete_units = []

            def _recurse_children(children, parent_name=""):
                for child in children:
                    if 'children' in child and child['children']:
                        _recurse_children(child['children'], child.get('display_name'))
                    else:
                        if child.get('complete'):
                            completed_units.append(parent_name + '--' + child.get('display_name'))
                        else:
                            incomplete_units.append(parent_name + '--' + child.get('display_name'))

            _recurse_children(blocks.get('children', []))
            return completed_units, incomplete_units

        # Constant across all learners in this report; fetch once instead of
        # per user (each call was a Site lookup + a throwaway request object).
        current_site = Site.objects.get_current()
        course_id_str = text_type(context.course_id)
        include_progress = context.include_progress_columns
        mode = context.progress_structure_mode

        with modulestore().bulk_operations(context.course_id):
            bulk_context = _CourseGradeBulkContext(context, users)

            # Uniform (A): the visible structure is the same within a cohort, so
            # the structure + total_block_types are computed once per cohort group
            # (keyed by cohort id) and reused; only completion varies per learner,
            # fetched for the whole batch in one query.
            cohort_cache = {}
            bulk_completion_keys = None
            bulk_completion_types = None
            if include_progress and mode == 'uniform' and users:
                bulk_completion_keys, bulk_completion_types = self._bulk_completions_with_types(
                    context.course_id, users,
                )

            # Fast flow: one bulk read of the stored per-learner course progress
            # (no structure walk) to fill the single Course Progress column.
            stored_progress = {}
            if not include_progress and users:
                stored_progress = self._bulk_stored_progress(context.course_id, users)

            success_rows, error_rows = [], []
            for user, course_grade, error in CourseGradeFactory().iter(
                users,
                course=context.course,
                collected_block_structure=context.course_structure,
                course_key=context.course_id,
            ):
                if not course_grade:
                    # An empty gradeset means we failed to grade a student.
                    error_rows.append([user.id, user.username, text_type(error)])
                    continue

                row = (
                    [user.id, user.email, user.username] +
                    self._user_enrollment_timestamp(user, context.course_id) +
                    self._user_grades(course_grade, context) +
                    self._user_cohort_group_names(user, context) +
                    self._user_experiment_group_names(user, context) +
                    self._user_team_names(user, bulk_context.teams) +
                    self._user_verification_mode(user, context, bulk_context.enrollments) +
                    self._user_certificate_info(user, context, course_grade, bulk_context.certs) +
                    [_user_enrollment_status(user, context.course_id)]
                )

                if include_progress:
                    row += self._progress_columns(
                        context, user, current_site, course_id_str, mode,
                        _flatten_course_block_tree, cohort_cache,
                        bulk_completion_keys, bulk_completion_types,
                    )
                else:
                    row.append(stored_progress.get(user.id, ''))

                success_rows.append(row)
            return success_rows, error_rows

    def _progress_columns(self, context, user, current_site, course_id_str, mode,
                          flatten_fn, cohort_cache, bulk_completion_keys, bulk_completion_types):
        """
        Compute the custom progress columns for a single learner in the selected
        ``mode`` (legacy / per_learner / uniform).
        """
        if mode == 'uniform':
            # (A) Structure + total_block_types computed once per *visibility
            # group* (exact legacy values), reused for every learner in that
            # group; only completion is overlaid per learner. The group key is
            # cohort + enrollment mode + split-test groups (all read-only, no
            # assignment side effects), covering content-group, enrollment-track
            # and split-test gating so the cached structure is correct per group.
            group_key = self._user_visibility_key(user, context)
            if group_key not in cohort_cache:
                rep_request = create_dummy_request(current_site, user)
                block_info, __ = get_progress_statistics_by_block_types(rep_request, course_id_str)
                structure_tree = get_course_outline_block_tree(
                    rep_request, course_id_str, None, allow_start_dates_in_future=True, lightweight=True,
                )
                cohort_cache[group_key] = {
                    'total_bt': block_info['total_block_types'],
                    'tree': structure_tree,
                }
            cached = cohort_cache[group_key]
            total_bt = cached['total_bt']
            total_completed_bt = self._completed_block_types_from_types(
                bulk_completion_types.get(user.id, []),
            )
            total_blocks = sum(total_bt.values())
            total_done = sum(total_completed_bt.values())
            user_progress = float(format((total_done / total_blocks) * 100, '.0f')) if total_blocks else 0.0
            if cached['tree']:
                tree = copy.deepcopy(cached['tree'])
                self._overlay_completion(tree, bulk_completion_keys.get(user.id, set()))
                completed_units, incomplete_units = flatten_fn(tree)
            else:
                completed_units, incomplete_units = [], []
        elif mode == 'per_learner':
            # (B) Per-learner visibility; lightweight tree (skips the outline
            # page's scored/graded/resume passes). Exact stats via the existing
            # helper.
            dummy_request = create_dummy_request(current_site, user)
            block_info, __ = get_progress_statistics_by_block_types(dummy_request, course_id_str)
            tree = get_course_outline_block_tree(
                dummy_request, course_id_str, user, allow_start_dates_in_future=True, lightweight=True,
            )
            user_progress = float(block_info['user_progress'])
            total_bt = block_info['total_block_types']
            total_completed_bt = block_info['total_completed_block_types']
            completed_units, incomplete_units = flatten_fn(tree)
        else:
            # Legacy: original two page-render helpers per learner.
            dummy_request = create_dummy_request(current_site, user)
            block_info, __ = get_progress_statistics_by_block_types(dummy_request, course_id_str)
            tree = get_course_outline_block_tree(
                dummy_request, course_id_str, user, allow_start_dates_in_future=True,
            )
            user_progress = float(block_info['user_progress'])
            total_bt = block_info['total_block_types']
            total_completed_bt = block_info['total_completed_block_types']
            completed_units, incomplete_units = flatten_fn(tree)

        return [user_progress, total_bt, total_completed_bt, completed_units, incomplete_units]

    @staticmethod
    def _bulk_stored_progress(course_key, users):
        """
        One query for the whole batch: the stored per-learner course progress
        from CourseProgressStats (refreshed by the progress-stats cron), keyed by
        user id and formatted as a whole-number percent string. Learners with no
        stored row map to nothing (rendered blank), signalling "not yet synced".
        """
        user_ids = [user.id for user in users]
        rows = CourseProgressStats.objects.filter(
            enrollment__course_id=course_key,
            enrollment__user_id__in=user_ids,
        ).values_list('enrollment__user_id', 'progress')
        return {
            user_id: u'{:.0f}'.format(progress)
            for user_id, progress in rows
            if progress is not None
        }

    @staticmethod
    def _bulk_completions_with_types(course_key, users):
        """
        One query for the whole batch. Returns two dicts:
          keys:  {user_id: set(completed block-key strings)}  -- for unit overlay
          types: {user_id: [completed block_type, ...]}        -- for block-type counts
        """
        rows = BlockCompletion.objects.filter(
            user_id__in=[u.id for u in users],
            context_key=course_key,
        ).values_list('user_id', 'block_key', 'block_type', 'completion')
        keys = defaultdict(set)
        types = defaultdict(list)
        for user_id, block_key, block_type, completion in rows:
            # total_completed_block_types (types) counts BlockCompletion row
            # existence, matching the legacy get_progress_information aggregate.
            types[user_id].append(block_type)
            # Unit completion (keys) requires a truthy completion value, matching
            # get_course_outline_block_tree's recurse_mark_complete.
            if completion:
                keys[user_id].add(text_type(block_key))
        return keys, types

    @staticmethod
    def _completed_block_types_from_types(block_types):
        """
        Categorize a learner's completed block types exactly like the legacy
        ``get_progress_information`` aggregate (independent per-category counts).
        """
        counts = {'video': 0, 'problem': 0, 'html': 0, 'other': 0}
        for block_type in block_types:
            if block_type in VIDEO_BLOCK_TYPES:
                counts['video'] += 1
            if block_type in PROBLEM_BLOCK_TYPES:
                counts['problem'] += 1
            if block_type == 'html':
                counts['html'] += 1
            if block_type not in CORE_BLOCK_TYPES:
                counts['other'] += 1
        return counts

    def _overlay_completion(self, block, completed_keys):
        """
        Mark completion on a shared block tree for a single learner, mirroring
        get_course_outline_block_tree's recurse_mark_complete: a leaf is complete
        if its key is in ``completed_keys``; a container is complete if all of its
        completable children are complete.
        """
        children = block.get('children')
        if children:
            for child in children:
                self._overlay_completion(child, completed_keys)
            completable = [c for c in children if c.get('type') not in COMPLETION_EXCLUDED_BLOCK_TYPES]
            block['complete'] = bool(completable) and all(c.get('complete') for c in completable)
        else:
            block['complete'] = text_type(block.get('id')) in completed_keys
        return block.get('complete')


class ProblemGradeReport(GradeReportBase):
    """
    Class to encapsulate functionality related to generating Problem Grade Reports.
    """

    @classmethod
    def generate(cls, _xmodule_instance_args, _entry_id, course_id, _task_input, action_name):
        """
        Public method to generate a grade report.
        """
        with modulestore().bulk_operations(course_id):
            context = _ProblemGradeReportContext(_xmodule_instance_args, _entry_id, course_id, _task_input, action_name)
            # pylint: disable=protected-access
            return ProblemGradeReport()._generate(context)

    def _generate(self, context):
        """
        Generate a CSV containing all students' problem grades within a given
        `course_id`.
        """
        context.update_status('ProblemGradeReport - 1: Starting problem grades')
        success_headers = self._success_headers(context)
        error_headers = self._error_headers()
        batched_rows = self._batched_rows(context)

        context.update_status('ProblemGradeReport - 2: Compiling grades')
        success_rows, error_rows = self._compile(context, batched_rows)
        context.update_status('ProblemGradeReport - 3: Uploading grades')
        self._upload(context, [success_headers] + success_rows, [error_headers] + error_rows)

        return context.update_status('ProblemGradeReport - 4: Completed problem grades')

    def _problem_grades_header(self):
        """Problem Grade report header."""
        return OrderedDict([('id', 'Student ID'), ('email', 'Email'), ('username', 'Username')])

    def _success_headers(self, context):
        """
        Returns headers for all gradable blocks including fixed headers
        for report.
        Returns:
            list: combined header and scorable blocks
        """
        header_row = list(self._problem_grades_header().values()) + ['Enrollment Status', 'Grade']
        return header_row + _flatten(list(context.graded_scorable_blocks_header.values()))

    def _error_headers(self):
        """
        Returns error headers for error report.
        Returns:
            list: error headers
        """
        return list(self._problem_grades_header().values()) + ['error_msg']

    def _rows_for_users(self, context, users):
        """
        Returns a list of rows for the given users for this report.
        """
        self.log_additional_info_for_testing(context, 'ProblemGradeReport: Starting to process new user batch.')
        success_rows, error_rows = [], []
        for student, course_grade, error in CourseGradeFactory().iter(
            users,
            course=context.course,
            collected_block_structure=context.course_structure,
            course_key=context.course_id,
        ):
            context.task_progress.attempted += 1
            if not course_grade:
                err_msg = text_type(error)
                # There was an error grading this student.
                if not err_msg:
                    err_msg = 'Unknown error'
                error_rows.append(
                    [student.id, student.email, student.username] +
                    [err_msg]
                )
                context.task_progress.failed += 1
                continue

            earned_possible_values = []
            for block_location in context.graded_scorable_blocks_header:
                try:
                    problem_score = course_grade.problem_scores[block_location]
                except KeyError:
                    earned_possible_values.append(['Not Available', 'Not Available'])
                else:
                    if problem_score.first_attempted:
                        earned_possible_values.append([problem_score.earned, problem_score.possible])
                    else:
                        earned_possible_values.append(['Not Attempted', problem_score.possible])

            context.task_progress.succeeded += 1
            enrollment_status = _user_enrollment_status(student, context.course_id)
            success_rows.append(
                [student.id, student.email, student.username] +
                [enrollment_status, course_grade.percent] +
                _flatten(earned_possible_values)
            )

        return success_rows, error_rows

    def _batched_rows(self, context):
        """
        A generator of batches of (success_rows, error_rows) for this report.
        """
        for users in self._batch_users(context):
            yield self._rows_for_users(context, users)
            # Clear the CourseEnrollment caches after each batch of users has been processed
            get_cache('get_enrollment').clear()
            get_cache(CourseEnrollment.MODE_CACHE_NAMESPACE).clear()


class ProblemResponses(object):
    """
    Class to encapsulate functionality related to generating Problem Responses Reports.
    """

    @classmethod
    def _build_problem_list(cls, course_blocks, root, path=None):
        """
        Generate a tuple of display names, block location paths and block keys
        for all problem blocks under the ``root`` block.
        Arguments:
            course_blocks (BlockStructureBlockData): Block structure for a course.
            root (UsageKey): This block and its children will be used to generate
                the problem list
            path (List[str]): The list of display names for the parent of root block
        Yields:
            Tuple[str, List[str], UsageKey]: tuple of a block's display name, path, and
                usage key
        """
        name = course_blocks.get_xblock_field(root, 'display_name') or root.category
        if path is None:
            path = [name]

        yield name, path, root

        for block in course_blocks.get_children(root):
            name = course_blocks.get_xblock_field(block, 'display_name') or block.category
            for result in cls._build_problem_list(course_blocks, block, path + [name]):
                yield result

    @classmethod
    def _build_student_data(cls, user_id, course_key, usage_key_str):
        """
        Generate a list of problem responses for all problem under the
        ``problem_location`` root.
        Arguments:
            user_id (int): The user id for the user generating the report
            course_key (CourseKey): The ``CourseKey`` for the course whose report
                is being generated
            usage_key_str (str): The generated report will include this
                block and it child blocks.
        Returns:
              Tuple[List[Dict], List[str]]: Returns a list of dictionaries
                containing the student data which will be included in the
                final csv, and the features/keys to include in that CSV.
        """
        usage_key = UsageKey.from_string(usage_key_str).map_into_course(course_key)
        user = get_user_model().objects.get(pk=user_id)
        course_blocks = get_course_blocks(user, usage_key)

        student_data = []
        max_count = settings.FEATURES.get('MAX_PROBLEM_RESPONSES_COUNT')

        store = modulestore()
        user_state_client = DjangoXBlockUserStateClient()

        student_data_keys = set()

        with store.bulk_operations(course_key):
            for title, path, block_key in cls._build_problem_list(course_blocks, usage_key):
                # Chapter and sequential blocks are filtered out since they include state
                # which isn't useful for this report.
                if block_key.block_type in ('sequential', 'chapter'):
                    continue

                block = store.get_item(block_key)
                generated_report_data = defaultdict(list)

                # Blocks can implement the generate_report_data method to provide their own
                # human-readable formatting for user state.
                if hasattr(block, 'generate_report_data'):
                    try:
                        user_state_iterator = user_state_client.iter_all_for_block(block_key)
                        for username, state in block.generate_report_data(user_state_iterator, max_count):
                            generated_report_data[username].append(state)
                    except NotImplementedError:
                        pass

                responses = []

                for response in list_problem_responses(course_key, block_key, max_count):
                    response['title'] = title
                    # A human-readable location for the current block
                    response['location'] = ' > '.join(path)
                    # A machine-friendly location for the current block
                    response['block_key'] = str(block_key)
                    # A block that has a single state per user can contain multiple responses
                    # within the same state.
                    user_states = generated_report_data.get(response['username'], [])
                    if user_states:
                        # For each response in the block, copy over the basic data like the
                        # title, location, block_key and state, and add in the responses
                        for user_state in user_states:
                            user_response = response.copy()
                            user_response.update(user_state)
                            student_data_keys = student_data_keys.union(list(user_state.keys()))
                            responses.append(user_response)
                    else:
                        responses.append(response)

                student_data += responses

                if max_count is not None:
                    max_count -= len(responses)
                    if max_count <= 0:
                        break

        # Keep the keys in a useful order, starting with username, title and location,
        # then the columns returned by the xblock report generator in sorted order and
        # finally end with the more machine friendly block_key and state.
        student_data_keys_list = (
            ['username', 'title', 'location'] +
            sorted(student_data_keys) +
            ['block_key', 'state']
        )

        return student_data, student_data_keys_list

    @classmethod
    def generate(cls, _xmodule_instance_args, _entry_id, course_id, task_input, action_name):
        """
        For a given `course_id`, generate a CSV file containing
        all student answers to a given problem, and store using a `ReportStore`.
        """
        start_time = time()
        start_date = datetime.now(UTC)
        num_reports = 1
        task_progress = TaskProgress(action_name, num_reports, start_time)
        current_step = {'step': 'Calculating students answers to problem'}
        task_progress.update_task_state(extra_meta=current_step)
        problem_location = task_input.get('problem_location')

        # Compute result table and format it
        student_data, student_data_keys = cls._build_student_data(
            user_id=task_input.get('user_id'),
            course_key=course_id,
            usage_key_str=problem_location
        )

        for data in student_data:
            for key in student_data_keys:
                data.setdefault(key, '')

        header, rows = format_dictlist(student_data, student_data_keys)

        task_progress.attempted = task_progress.succeeded = len(rows)
        task_progress.skipped = task_progress.total - task_progress.attempted

        rows.insert(0, header)

        current_step = {'step': 'Uploading CSV'}
        task_progress.update_task_state(extra_meta=current_step)

        # Perform the upload
        problem_location = re.sub(r'[:/]', '_', problem_location)
        csv_name = 'student_state_from_{}'.format(problem_location)
        report_name = upload_csv_to_report_store(rows, csv_name, course_id, start_date)
        current_step = {'step': 'CSV uploaded', 'report_name': report_name}

        return task_progress.update_task_state(extra_meta=current_step)
