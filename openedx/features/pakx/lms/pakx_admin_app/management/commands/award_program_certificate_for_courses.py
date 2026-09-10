"""
Management command to award a program certificate to learners based on an
*explicit, operator-supplied set of course runs* — overriding the program's
configured course list in the catalog.

Why this exists
---------------
The normal award path (``award_program_certificates`` -> ``ProgramProgressMeter``)
requires the learner to hold a passing certificate for *every* course configured
in the program (see ``_available_date_for_program`` in
``openedx/core/djangoapps/programs/utils.py``). If a course was misconfigured in
the program — e.g. the same content published under two course numbers such as
``...+13`` and ``...+13_2`` — the program's course list diverges from what the
cohort actually completed. Learners finish the correct run, but because the
program still references the stale/duplicate course, the meter never marks the
program complete and no program certificate is triggered.

This command lets an operator say: "for THIS program, treat exactly THESE course
runs as the requirement." If a learner holds a passing, available certificate for
every course id passed, we award the program certificate through the credentials
service and send the completion email (reusing the existing email logic).

As a safety net it also honors the standard path: any learner the normal
ProgramProgressMeter already considers complete for this program is awarded too
(reason "meter"), using the program's real completion date. When both apply the
meter reason wins. This catches config-correct learners whose award never fired,
without a second command run.

Preconditions
-------------
* The program (``--program-uuid``) exists in the catalog cache
  (run ``./manage.py lms cache_programs`` if stale).
* An **active ProgramCertificate** is configured for this program_uuid in the
  credentials service, otherwise the credentials POST is rejected. See
  ``credentials/apps/api/v2/serializers.py``.

Examples
--------
Dry run (report only)::

    ./manage.py lms award_program_certificate_for_courses \
        --program-uuid 12345678-1234-1234-1234-123456789012 \
        --course-ids course-v1:Org+13+2024 course-v1:Org+CoreA+2024

Commit + optionally restrict to specific learners::

    ./manage.py lms award_program_certificate_for_courses \
        --program-uuid 12345678-1234-1234-1234-123456789012 \
        --course-ids course-v1:Org+13+2024 course-v1:Org+CoreA+2024 \
        --usernames alice bob \
        --commit
"""

from logging import getLogger

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey

from lms.djangoapps.certificates.models import GeneratedCertificate
from openedx.core.djangoapps.catalog.utils import get_programs
from openedx.core.djangoapps.certificates.api import available_date_for_certificate
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.credentials.models import CredentialsApiConfig
from openedx.core.djangoapps.credentials.utils import get_credentials_api_client
from openedx.core.djangoapps.programs.tasks.v1.tasks import (
    award_program_certificate,
    get_certified_programs,
    get_completed_programs,
    send_program_certificate_email,
)

log = getLogger(__name__)

# How a learner qualified, for logging/reporting.
REASON_OVERRIDE = 'override'  # passed the operator-supplied course-ids
REASON_METER = 'meter'        # standard ProgramProgressMeter marks the program complete


class Command(BaseCommand):
    """Award a program certificate based on an explicit set of course runs."""

    help = (
        "Award a program certificate to learners who hold passing certificates for an "
        "operator-supplied set of course runs, bypassing the program's configured course list."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--program-uuid',
            required=True,
            help='UUID of the program to award.',
        )
        parser.add_argument(
            '--course-ids',
            nargs='+',
            required=True,
            help='Course run keys treated as the program requirement (space separated).',
        )
        parser.add_argument(
            '--usernames',
            nargs='+',
            default=None,
            help='Restrict to these learners. Default: every learner passing all --course-ids.',
        )
        parser.add_argument(
            '-c', '--commit',
            action='store_true',
            default=False,
            help='Actually award. Without this flag the command only reports (dry run).',
        )

    def handle(self, *args, **options):
        program_uuid = options['program_uuid']
        commit = options['commit']

        if not CredentialsApiConfig.current().is_learner_issuance_enabled:
            raise CommandError('Credentials issuance is disabled in CredentialsApiConfig. Aborting.')

        # Validate the program exists in the catalog (also used for the email content).
        program = get_programs(uuid=program_uuid)
        if not program:
            raise CommandError('No program found in the catalog for uuid {}.'.format(program_uuid))

        course_keys = self._parse_course_keys(options['course_ids'])
        log.info(
            'Awarding program %s ("%s") against %d override course run(s): %s',
            program_uuid, program.get('title'), len(course_keys), [str(k) for k in course_keys],
        )

        # username -> (reason, visible_date). Meter path wins when both qualify,
        # since it carries the program's real completion date.
        candidates = self._collect_candidates(program, program_uuid, course_keys, options['usernames'])
        log.info(
            'Eligible: %d learner(s) [%d via meter, %d via override course-ids].',
            len(candidates),
            sum(1 for r, _ in candidates.values() if r == REASON_METER),
            sum(1 for r, _ in candidates.values() if r == REASON_OVERRIDE),
        )
        log.info('Candidate usernames to be attempted (%d): %s', len(candidates), sorted(candidates))

        if not commit:
            for username in sorted(candidates):
                reason, visible_date = candidates[username]
                log.info('[dry-run] Would award %s to %s (%s, visible_date=%s)',
                         program_uuid, username, reason, visible_date)
            log.info('Dry run complete. Re-run with --commit to award %d learner(s).', len(candidates))
            return

        client = get_credentials_api_client(
            User.objects.get(username=settings.CREDENTIALS_SERVICE_USERNAME),
        )

        awarded, skipped, failed = 0, 0, 0
        for username in sorted(candidates):
            reason, visible_date = candidates[username]
            try:
                student = User.objects.get(username=username)

                # Idempotency: skip learners who already hold this program certificate.
                if program_uuid in get_certified_programs(student):
                    log.info('Skipping %s; program certificate already awarded.', username)
                    skipped += 1
                    continue

                response = award_program_certificate(client, username, program_uuid, visible_date)
                send_program_certificate_email(student, program_uuid, response.get('uuid'))
                log.info('Awarded program %s to %s (%s, visible_date=%s).',
                         program_uuid, username, reason, visible_date)
                awarded += 1
            except Exception:  # pylint: disable=broad-except
                log.exception('Failed to award program %s to %s.', program_uuid, username)
                failed += 1

        log.info('Done. Awarded: %d, skipped (already had): %d, failed: %d.', awarded, skipped, failed)

    def _parse_course_keys(self, course_ids):
        """Validate and convert the passed course id strings into CourseKeys."""
        keys = []
        for course_id in course_ids:
            try:
                keys.append(CourseKey.from_string(course_id))
            except InvalidKeyError:
                raise CommandError('Invalid course id: {}'.format(course_id))
        return keys

    def _collect_candidates(self, program, program_uuid, course_keys, usernames):
        """
        Build ``{username: (reason, visible_date)}`` for every eligible learner.

        A learner qualifies if EITHER:
          * override  - they hold a passing, available certificate for every
            course run in ``course_keys`` (the operator-supplied requirement), OR
          * meter     - the standard ProgramProgressMeter already marks this
            program complete for them (catches config-correct learners whose
            award never fired).

        When both apply, the meter reason wins because it carries the program's
        real completion date.
        """
        # Candidate pool = anyone with a passing cert in an override course OR in
        # any course run configured on the program. Narrow to --usernames if given.
        program_run_keys = self._program_run_keys(program)
        pool_keys = set(course_keys) | program_run_keys

        certs = GeneratedCertificate.eligible_available_certificates.filter(course_id__in=pool_keys)
        if usernames:
            certs = certs.filter(user__username__in=usernames)
        pool_usernames = set(certs.values_list('user__username', flat=True).distinct())

        # Which override learners pass ALL the supplied course-ids.
        override_usernames = self._override_usernames(course_keys, usernames)

        candidates = {}
        for username in pool_usernames:
            student = User.objects.get(username=username)

            meter_date = self._meter_visible_date(student, program_uuid)
            if meter_date is not None:
                candidates[username] = (REASON_METER, meter_date)
            elif username in override_usernames:
                candidates[username] = (REASON_OVERRIDE, self._visible_date(student, course_keys))

        return candidates

    def _override_usernames(self, course_keys, usernames):
        """
        Usernames of learners holding a passing, available certificate for *every*
        course run in ``course_keys``. ``eligible_available_certificates`` already
        filters to passing status past the available date, matching the meter.
        """
        certs = GeneratedCertificate.eligible_available_certificates.filter(course_id__in=course_keys)
        if usernames:
            certs = certs.filter(user__username__in=usernames)

        passing_courses_by_user = {}
        for uname, course_id in certs.values_list('user__username', 'course_id'):
            passing_courses_by_user.setdefault(uname, set()).add(course_id)

        required = set(course_keys)
        return {uname for uname, passed in passing_courses_by_user.items() if required.issubset(passed)}

    def _program_run_keys(self, program):
        """All course run keys configured on the program in the catalog."""
        keys = set()
        for course in program.get('courses', []):
            for course_run in course.get('course_runs', []):
                try:
                    keys.add(CourseKey.from_string(course_run['key']))
                except InvalidKeyError:
                    log.warning('Skipping malformed course run key in program: %s', course_run.get('key'))
        return keys

    def _meter_visible_date(self, student, program_uuid):
        """
        Return the program's completion date per the standard meter, or None if the
        meter does not consider this program complete for the learner.
        """
        completed = {}
        for site in Site.objects.all():
            completed.update(get_completed_programs(site, student))
        return completed.get(program_uuid)

    def _visible_date(self, student, course_keys):
        """
        Compute the program's visible date the same way the meter does: the latest
        of each required course's certificate available date.
        """
        certs = GeneratedCertificate.eligible_available_certificates.filter(
            user=student, course_id__in=course_keys,
        )
        dates = []
        for cert in certs:
            try:
                overview = CourseOverview.get_from_id(cert.course_id)
            except (CourseOverview.DoesNotExist, IOError):
                log.warning('No course overview for %s; skipping its date.', cert.course_id)
                continue
            dates.append(available_date_for_certificate(overview, cert))
        # award_program_certificate serializes visible_date, so never hand it None.
        return max(dates) if dates else timezone.now()
