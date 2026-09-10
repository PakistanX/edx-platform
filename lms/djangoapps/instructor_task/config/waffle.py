"""
This module contains various configuration settings via
waffle switches for the instructor_task app.
"""


from openedx.core.djangoapps.waffle_utils import WaffleFlagNamespace, WaffleSwitchNamespace

WAFFLE_NAMESPACE = u'instructor_task'
INSTRUCTOR_TASK_WAFFLE_FLAG_NAMESPACE = WaffleFlagNamespace(name=WAFFLE_NAMESPACE)
WAFFLE_SWITCHES = WaffleSwitchNamespace(name=WAFFLE_NAMESPACE)

# Waffle switches
OPTIMIZE_GET_LEARNERS_FOR_COURSE = u'optimize_get_learners_for_course'
GENERATE_GRADE_REPORT_VERIFIED_ONLY = u'generate_grade_report_for_verified_only'
# Compute the custom progress columns (Course Progress / block types / completed
# & incomplete units) from a single per-learner outline tree instead of two heavy
# page-render helpers. Preserves per-learner block visibility.
OPTIMIZE_GRADE_REPORT_PROGRESS_COLUMNS = u'optimize_grade_report_progress_columns'
# Only meaningful together with the switch above: build the block structure once
# for the whole course and overlay each learner's completion, instead of applying
# per-learner access transforms. Fastest, but only correct when the course is NOT
# gated per learner (cohort/partition/start-date content).
GRADE_REPORT_UNIFORM_BLOCK_STRUCTURE = u'grade_report_uniform_block_structure'
# Fan the grade report out across parallel Celery subtasks (one per learner
# chunk), each writing a partial CSV that a finalize step concatenates, so the
# report uses all available worker cores instead of a single sequential task.
PARALLELIZE_GRADE_REPORT = u'parallelize_grade_report'
# Expose advanced batch controls (custom batch size + a start/end learner-row
# range) on the grade-report options. Gated additionally to Django superusers in
# the view; this switch only makes the controls available at all.
GRADE_REPORT_BATCH_RANGE = u'grade_report_batch_range'


def waffle_flags():
    """
    Returns the namespaced, cached, audited Waffle flags dictionary for Grades.
    """
    return {}


def optimize_get_learners_switch_enabled():
    """
    Returns True if optimize get learner switch is enabled, otherwise False.
    """
    return WAFFLE_SWITCHES.is_enabled(OPTIMIZE_GET_LEARNERS_FOR_COURSE)


def optimize_grade_report_progress_columns_enabled():
    """
    Returns True if the optimized (single-tree) progress-column path is enabled.
    """
    return WAFFLE_SWITCHES.is_enabled(OPTIMIZE_GRADE_REPORT_PROGRESS_COLUMNS)


def grade_report_uniform_block_structure_enabled():
    """
    Returns True if the uniform (shared, non-per-learner) block structure path is
    enabled. As a *default*, it is overridden by
    ``optimize_grade_report_progress_columns`` (per_learner) -- see
    ``default_progress_structure_mode`` -- but 'uniform' can always be chosen
    explicitly per report from the instructor dashboard.
    """
    return WAFFLE_SWITCHES.is_enabled(GRADE_REPORT_UNIFORM_BLOCK_STRUCTURE)


def default_progress_structure_mode():
    """
    Single source of truth for the default block-structure computation mode used
    by the grade report's custom progress columns, resolved from the waffle
    switches with a deterministic precedence:

        optimize waffle ON           -> 'per_learner'   (wins over the rest)
        else uniform waffle ON       -> 'uniform'
        else                         -> 'legacy'

    per_learner wins over uniform on purpose: it is always correct, whereas
    uniform is the faster path that is only correct for courses not gated per
    learner, so it should never become the default just because both switches
    happen to be on.

    Both the backend (``_CourseGradeReportContext``) and the instructor
    dashboard dropdown read this, so the pre-selected UI option always matches
    the mode the backend would actually use, and conflicting switches resolve
    the same way in both places.
    """
    if optimize_grade_report_progress_columns_enabled():
        return 'per_learner'
    if grade_report_uniform_block_structure_enabled():
        return 'uniform'
    return 'legacy'


def parallelize_grade_report_enabled():
    """
    Returns True if the grade report should be fanned out across parallel
    subtasks (one per learner chunk) instead of run as a single sequential task.
    """
    return WAFFLE_SWITCHES.is_enabled(PARALLELIZE_GRADE_REPORT)


def grade_report_batch_range_enabled():
    """
    Returns True if the advanced grade-report batch controls (custom batch size
    and start/end learner-row range) are available. The view further restricts
    these to Django superusers.
    """
    return WAFFLE_SWITCHES.is_enabled(GRADE_REPORT_BATCH_RANGE)


def generate_grade_report_for_verified_only():
    """
    Returns True if waffle switch is enabled that indicates generate grading reports only for
    verified learners.
    """
    return WAFFLE_SWITCHES.is_enabled(GENERATE_GRADE_REPORT_VERIFIED_ONLY)
