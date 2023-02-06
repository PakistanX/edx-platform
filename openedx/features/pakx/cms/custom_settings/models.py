"""
All models for custom settings app
"""

from collections import OrderedDict
from logging import getLogger
from uuid import uuid4

from django.conf import settings
from django.db import models
from django.utils.lru_cache import lru_cache
from jsonfield.fields import JSONField
from model_utils.models import TimeStampedModel
from organizations.models import Organization
from django.utils.translation import ugettext_lazy as _
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview

logger = getLogger(__name__)  # pylint: disable=invalid-name


class CourseSet(TimeStampedModel):
    """
    Set of courses linked to a specific Organization
    """

    name = models.CharField(max_length=64)
    is_active = models.BooleanField(default=True, db_index=True)
    description = models.TextField(null=True, blank=True)
    logo_url = models.CharField(max_length=256, blank=True, null=True)
    video_url = models.CharField(max_length=256, blank=True, null=True)
    publisher_org = models.ForeignKey(Organization, on_delete=models.DO_NOTHING)

    def __str__(self):
        return "{} status:{}".format(self.name, self.is_active)


class CourseOverviewContent(TimeStampedModel):
    NORMAL = 0
    VIDEO = 1

    COURSE_EXPERIENCES = (
        (NORMAL, 'Normal'),
        (VIDEO, 'Video')
    )

    body_html = models.TextField(blank=True, default='')
    card_description = models.CharField(max_length=256, blank=True)
    publisher_logo_url = models.CharField(max_length=256, blank=True, null=True)
    group_enrollment_url = models.CharField(max_length=256, blank=True, null=True)
    course_set = models.ForeignKey(CourseSet, on_delete=models.DO_NOTHING, default=None, null=True, blank=True)
    course_experience = models.PositiveSmallIntegerField(default=NORMAL, choices=COURSE_EXPERIENCES)
    course = models.OneToOneField(CourseOverview, related_name='custom_settings', on_delete=models.CASCADE)
    subsection_to_lock = models.CharField(max_length=256, blank=True, null=True)
    days_to_unlock = models.PositiveSmallIntegerField(default=0)
    course_banner_image_url = models.CharField(max_length=256, blank=True, default='', null=True)
    is_public = models.BooleanField('Course is public and should be available on publisher spaces', default=False)
    publisher_name = models.CharField(max_length=128, blank=True, default='', null=True)
    publisher_card_logo_url = models.CharField(max_length=256, blank=True, default='', null=True)
    publisher_description = models.TextField(null=True, blank=True)
    about_page_image_url = models.CharField(max_length=256, blank=True, default='', null=True)
    days_till_next_reminder = models.PositiveSmallIntegerField(default=0)
    reminder_stop_date = models.DateField(default=None, null=True, blank=True)
    course_for_you_html = models.TextField(blank=True, default='')
    instructors_html = models.TextField(blank=True, default='')
    certificate_html = models.TextField(blank=True, default='')
    offered_by_html = models.TextField(blank=True, default='')
    reviews_html = models.TextField(blank=True, default='')
    faq_html = models.TextField(blank=True, default='')

    def __str__(self):
        return 'CourseOverviewContent for course {id}'.format(id=self.course.id)


class PartnerSpace(TimeStampedModel):
    """
    Partner space model for Partner Meta
    """

    name = models.CharField(max_length=128, unique=True)
    organization = models.OneToOneField(Organization, on_delete=models.CASCADE, related_name='partner_space')
    footer_links = JSONField(null=False, blank=True, default=dict, load_kwargs={'object_pairs_hook': OrderedDict})
    partner_meta = JSONField(null=False, blank=True, default=dict, load_kwargs={'object_pairs_hook': OrderedDict})

    def __str__(self):
        return self.name

    @staticmethod
    def _get_value(dict_obj, name, default=None):
        """
        Return Partner Meta value for the key specified as name argument.

        :params name: (str) Name of the key for which to return configuration value.
        :params dict_obj: (Dict) Dict object from which value should be extracted for given Key.
        :params default: default value tp return if key is not found in the configuration

        :returns: Partner meta value for the given key or returns `None` if configuration is not enabled.
        """
        try:
            return dict_obj.get(name, default)
        except AttributeError as error:
            logger.exception(u'Invalid JSON data. \n [%s]', error)

        return default

    def get_footer_value(self, name, default=None):
        return self._get_value(self.footer_links, name, default)

    def get_space_meta(self, name, default=None):
        return self._get_value(self.partner_meta, name, default)

    @classmethod
    @lru_cache(maxsize=16)
    def get_partner_space(cls, space_name):
        """
        This returns a SiteConfiguration object which has an org_filter that matches
        the supplied org

        :param space_name: (str) Name of the space
        :returns: (PartnerSpace) model object or None
        """

        logger.info('Loading partner space for :"{}"'.format(space_name))
        partner = cls.objects.filter(name__iexact=space_name).select_related('organization').first()
        if not partner:
            default_space = settings.DEFAULT_PUBLIC_PARTNER_SPACE
            logger.warning('Partner space for:"{}" not found, Loading Default:"{}"'.format(space_name, default_space))
            partner = cls.objects.filter(name__iexact=default_space).select_related('organization').first()
        return partner


class ProgramCustomData(TimeStampedModel):
    """This model holds Our added HTML fields for various programs."""
    program_uuid = models.UUIDField(blank=True, default=uuid4, editable=False, unique=True, verbose_name=_('UUID'))
    program_for_you_html = models.TextField(blank=True, default='')
    instructors_html = models.TextField(blank=True, default='')
    certificate_html = models.TextField(blank=True, default='')
    offered_by_html = models.TextField(blank=True, default='')
    reviews_html = models.TextField(blank=True, default='')
    faq_html = models.TextField(blank=True, default='')
    image_url = models.CharField(max_length=256, blank=True, null=True)
    group_enrollment_url = models.CharField(max_length=256, blank=True, null=True)
    video_url = models.CharField(max_length=256, blank=True, null=True)

    def __str__(self):
        """String representation of this model."""
        return str(self.program_uuid)
