"""
Management command to create & enroll users for Dialog Academy and send a single email containing all the details.
"""

import json
import requests
from csv import DictReader
from io import StringIO
from logging import getLogger

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from edx_ace import Recipient, ace
from opaque_keys.edx.keys import CourseKey

from openedx.core.djangoapps.ace_common.template_context import get_base_template_context
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.lib.celery.task_utils import emulate_http_request
from student.models import CourseEnrollment

from ...message_types import DialogAcademyUserEnrollmentNotification
from ...tasks import bulk_user_registration
from ...utils import get_user_data_from_bulk_registration_file

log = getLogger(__name__)

DIALOG_ACADEMY_ORG_ID = 39
DEFAULT_REQUESTER_EMAIL = "ahmad.shafique@arbisoft.com"
if settings.DEBUG:
    DIALOG_ACADEMY_ORG_ID = 1
    DEFAULT_REQUESTER_EMAIL = "edx@example.com"

PROGRAM_GUIDELINES_LINK = "https://drive.google.com/file/d/1rXZibNtfVEHIVLSzaNqA1BrrEZSvNeTW/view?usp=sharing"
CYBERBULLYING_AVOIDANCE_LINK = "https://drive.google.com/file/d/1PpVZkO02BmO_HRoPcEOykd60X4bu1Cjj/view?usp=sharing"


class Command(BaseCommand):
    """
    A management command that will create & enroll users for Dialog Academy from the \
    csv bulk enrollment file link and send every user a single email containing all the details.
    """

    help = 'Create and enroll users in course for Dialog academy'

    def add_arguments(self, parser):
        parser.add_argument(
            '--csv_url',
            required=True,
            type=str,
            help='Bulk enrollment csv sheet url',
        )
        parser.add_argument(
            '--enroll_in_course_id',
            required=True,
            type=str,
            help='Course ID of course to enroll in'
        )
        parser.add_argument(
            '--course_datetime',
            required=True,
            type=str,
            help='String containing course datetime',
        )
        parser.add_argument(
            '--google_meet_link',
            required=True,
            type=str,
            help='Google Meet link',
        )
        parser.add_argument(
            '--org_id',
            type=int,
            help='Default organization ID',
        )
        parser.add_argument(
            '--requester_email',
            type=str,
            help='Default requester email',
        )
        parser.add_argument(
            '--program_guidelines_link',
            type=str,
            help='Default program_guidelines_link',
        )
        parser.add_argument(
            '--cyberbulling_avoidance_link',
            type=str,
            help='Default cyberbulling_avoidance_link',
        )

    def handle(self, *args, **options):
        log.info('Staring command to download bulk enrollment csv file for creating and enrolling users in dialog academy courses')

        site = Site.objects.get_current()
        course_key_string = options.get('enroll_in_course_id')
        course_key = CourseKey.from_string(course_key_string)
        course_overview = CourseOverview.objects.get(id=course_key)
        now = timezone.now()
        if course_overview.enrollment_start and now < course_overview.enrollment_start or \
            course_overview.enrollment_end and now > course_overview.enrollment_end:
            log.error('Course is not open for enrollment. Aborting user creation and enrollment, email not sent!')
            return

        protocol = 'https://' if not settings.DEBUG else 'http://'
        csv_url = options.get('csv_url')
        response = requests.get(csv_url)
        response.raise_for_status()

        session_timeline = options.get('course_datetime'),
        google_meet_link = options.get('google_meet_link'),
            
        file_data = StringIO(response.content.decode('utf-8'))
        file_reader = DictReader(file_data)

        required_col_names = {'name', 'username', 'email', 'organization_id', 'role', 'employee_id', 'language', 'verified'}
        if not required_col_names.issubset(set(file_reader.fieldnames) or []):
            log.error('Invalid column names in {}! Correct names are: "{}"'.format(csv_url, '" | "'.join(required_col_names))),
            return
        
        default_requester = options.get('requester_email') or DEFAULT_REQUESTER_EMAIL
        users = get_user_data_from_bulk_registration_file(file_reader, default_org_id=(options.get('org_id') or DIALOG_ACADEMY_ORG_ID))
        bulk_user_registration(users, default_requester, send_creation_email=False)

        request_user = User.objects.filter(email=default_requester).first()
        for _, user in enumerate(users, start=1):
            try:
                user_email = user.get('email')
                enroll_user = User.objects.get(email=user_email)
                
                try:
                    with transaction.atomic():
                        CourseEnrollment.enroll(enroll_user, course_key, check_access=True)
                except Exception as e:
                    log.exception('User {} is already enrolled in course {}. Sending email only! --- {}'.format(user_email, course_key_string, str(e)))

                email_context = {
                    'course': course_overview.display_name,
                    'image_url': protocol + site.domain + course_overview.course_image_url,
                    'url': "{}{}/courses/{}/overview".format(protocol, site.domain, course_key_string),
                    'site_name': site.domain,
                    'dashboard_url': '{}{}/dashboard'.format(protocol, site.domain),
                    'platform_name': configuration_helpers.get_value('PLATFORM_NAME', settings.PLATFORM_NAME),
                    'user_password': user.get('user_password', ''),
                    'session_timeline': session_timeline,
                    'google_meet_link': google_meet_link,
                    'program_guidelines_link': options.get('program_guidelines_link') or PROGRAM_GUIDELINES_LINK,
                    'cyberbulling_avoidance_link': options.get('cyberbulling_avoidance_link') or CYBERBULLYING_AVOIDANCE_LINK,
                }
                context = get_base_template_context(site, user=enroll_user)
                context.update(email_context)

                with emulate_http_request(site, request_user):
                    message = DialogAcademyUserEnrollmentNotification().personalize(
                        recipient=Recipient(context['username'], context['email']),
                        language='en',
                        user_context=context,
                    )
                    ace.send(message)
            except Exception as e:  # pylint: disable=broad-except
                log.exception('Failed to enroll user in course --- {}'.format(str(e)))
