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
    enabled. Only honored when ``optimize_grade_report_progress_columns`` is on.
    """
    return WAFFLE_SWITCHES.is_enabled(GRADE_REPORT_UNIFORM_BLOCK_STRUCTURE)


def parallelize_grade_report_enabled():
    """
    Returns True if the grade report should be fanned out across parallel
    subtasks (one per learner chunk) instead of run as a single sequential task.
    """
    return WAFFLE_SWITCHES.is_enabled(PARALLELIZE_GRADE_REPORT)


def generate_grade_report_for_verified_only():
    """
    Returns True if waffle switch is enabled that indicates generate grading reports only for
    verified learners.
    """
    return WAFFLE_SWITCHES.is_enabled(GENERATE_GRADE_REPORT_VERIFIED_ONLY)
