"""
All models for custom settings app
"""
from django.db import models
from model_utils.models import TimeStampedModel
from organizations.models import Organization

from openedx.core.djangoapps.content.course_overviews.models import CourseOverview


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
    course_set = models.ForeignKey(CourseSet, on_delete=models.DO_NOTHING, default=None, null=True, blank=True)
    course_experience = models.PositiveSmallIntegerField(default=NORMAL, choices=COURSE_EXPERIENCES)
    course = models.OneToOneField(CourseOverview, related_name='custom_settings', on_delete=models.CASCADE)
    subsection_to_lock = models.CharField(max_length=256, blank=True, null=True)
    days_to_unlock = models.IntegerField(default=0)

    is_public = models.BooleanField('Course is public and should be available on publisher spaces', default=False)
    publisher_name = models.CharField(max_length=128, blank=True, default='', null=True)
    publisher_card_logo_url = models.CharField(max_length=256, blank=True, default='', null=True)

    def __str__(self):
        return 'CourseOverviewContent for course {id}'.format(id=self.course.id)
