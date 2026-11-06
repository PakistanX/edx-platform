"""
Models for the PakX Admin Panel app.
"""
import uuid

from django.core.exceptions import ValidationError
from django.db import models
from organizations.models import Organization

from openedx.core.djangoapps.content.course_overviews.models import CourseOverview


class TrendingCourse(models.Model):
    """
    A course curated to appear in the dashboard's "Trending Courses" tab.

    Global (site-wide) list, managed from Django admin. One row per course;
    `position` controls display order and `is_active` toggles visibility
    without deleting the row.
    """
    course = models.OneToOneField(
        CourseOverview, on_delete=models.CASCADE, related_name='trending_entry',
    )
    position = models.PositiveIntegerField(default=0, help_text="Lower shows first.")
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = 'pakx_admin_app'
        ordering = ['position', '-created_at']

    def __str__(self):
        return '{} (pos {}){}'.format(self.course_id, self.position, '' if self.is_active else ' [inactive]')


class MAUReport(models.Model):
    """
    Metadata for a generated Monthly Active Users (MAU) report.

    One row per (organization, month). For the overall (all-organizations)
    aggregate report `is_overall` is True and `organization_short_name` is blank.
    The CSV itself lives in storage (S3 in production) at `file_path`.
    """
    organization_short_name = models.CharField(
        max_length=255, blank=True, default='',
        help_text="Org short_name this report covers; blank for the overall (all-orgs) report.",
    )
    organization_name = models.CharField(max_length=255, blank=True, default='')
    is_overall = models.BooleanField(default=False)
    # First day of the reported month (the month whose activity is covered).
    month = models.DateField(help_text="First day of the reported month.")
    active_user_count = models.PositiveIntegerField(default=0)
    excluded_staff_count = models.PositiveIntegerField(default=0)
    file_path = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = 'pakx_admin_app'
        unique_together = ('organization_short_name', 'is_overall', 'month')
        ordering = ['-month', 'organization_name']

    def __str__(self):
        scope = 'ALL' if self.is_overall else self.organization_short_name
        return '{} MAU {}'.format(scope, self.month.strftime('%Y-%m'))


class MAUReportExcludedUser(models.Model):
    """
    Extra usernames to exclude from MAU reports for a given org, on top of the
    automated exclusion of Django staff/superusers and course staff (users holding
    a CourseAccessRole in the org). Editable via Django admin for edge cases.
    """
    organization_short_name = models.CharField(
        max_length=255, blank=True, default='',
        help_text="Org short_name; leave blank to exclude the username from every org's report.",
    )
    username = models.CharField(max_length=150)

    class Meta:
        app_label = 'pakx_admin_app'
        unique_together = ('organization_short_name', 'username')

    def __str__(self):
        return '{}:{}'.format(self.organization_short_name or 'ALL', self.username)


class CourseLinkClient(models.Model):
    """
    A client (partner) that is given tracked links to course pages.

    ``organization`` ties the client to an LMS organization so the admin-panel
    accounts of that organization only see this client's links. ``report_token``
    guards the public, read-only report page shared with the client.
    """
    name = models.CharField(max_length=255, unique=True)
    organization = models.ForeignKey(
        Organization, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
        help_text="Admin-panel accounts of this organization can view this client's links and stats.",
    )
    report_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    is_report_enabled = models.BooleanField(
        default=True, help_text="Untick to switch off the public report page for this client.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = 'pakx_admin_app'
        ordering = ['name']

    def __str__(self):
        return self.name


class CourseLink(models.Model):
    """
    A tracked way into a course's custom landing page (/trainings/<slug>/ or /courses/<slug>/).

    * alias: ``value`` is an extra slug (e.g. ``business-planning-acme``) that serves the same
      page as the canonical slug.
    * utm: ``value`` is a ``utm_source`` (e.g. ``acme``) appended to the canonical URL, optionally with
      ``utm_medium`` / ``utm_campaign`` / ``utm_term`` / ``utm_content``. A visit counts for the most
      specific registered link whose filled-in parameters all match; blank ones match anything.

    Aliases are unique across all links; a utm source can be reused for different courses and
    parameter combinations.
    """
    UTM_EXTRA_FIELDS = ('utm_medium', 'utm_campaign', 'utm_term', 'utm_content')
    ALIAS = 'alias'
    UTM = 'utm'
    KIND_CHOICES = ((ALIAS, 'Alias slug'), (UTM, 'UTM source'))

    client = models.ForeignKey(CourseLinkClient, on_delete=models.CASCADE, related_name='links')
    # No DB constraint and no cascade, like student.CourseEnrollment.course: Studio (which does not install this app)
    # deletes course overviews too, and a deleted course must not take a client's stats with it.
    course = models.ForeignKey(
        CourseOverview, db_constraint=False, on_delete=models.DO_NOTHING, related_name='tracked_links'
    )
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    value = models.CharField(max_length=100, db_index=True, help_text="Lowercase letters, digits and dashes.")
    utm_medium = models.CharField(max_length=50, blank=True, default='')
    utm_campaign = models.CharField(max_length=50, blank=True, default='')
    utm_term = models.CharField(max_length=50, blank=True, default='')
    utm_content = models.CharField(max_length=50, blank=True, default='')
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # Mirrors ``value`` for alias links only (NULL for utm links), so the database itself keeps an alias
    # unique across courses: a conditional unique index is not available on MySQL, but NULLs never collide.
    alias_slug = models.CharField(max_length=100, unique=True, null=True, blank=True, editable=False)

    class Meta:
        app_label = 'pakx_admin_app'
        unique_together = ('kind', 'value', 'course', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content')
        ordering = ['client__name', 'kind', 'value']

    @property
    def label(self):
        """Short human name: ``Alias: x`` or ``UTM: source / medium / campaign``."""
        if self.kind == self.ALIAS:
            return 'Alias: {}'.format(self.value)
        parts = [self.value] + [getattr(self, field) for field in self.UTM_EXTRA_FIELDS if getattr(self, field)]
        return 'UTM: {}'.format(' / '.join(parts))

    def __str__(self):
        return '{} -> {}'.format(self.label, self.course_id)

    def save(self, *args, **kwargs):
        self.value = (self.value or '').strip().lower()
        for field in self.UTM_EXTRA_FIELDS:
            setattr(self, field, (getattr(self, field) or '').strip().lower() if self.kind == self.UTM else '')
        self.alias_slug = self.value if self.kind == self.ALIAS else None
        update_fields = kwargs.get('update_fields')
        if update_fields is not None and ('value' in update_fields or 'kind' in update_fields):
            kwargs['update_fields'] = set(update_fields) | {'alias_slug'} | set(self.UTM_EXTRA_FIELDS)
        super(CourseLink, self).save(*args, **kwargs)

    def clean(self):
        """Same rules as the admin-panel API (called by the Django admin's form validation)."""
        from .course_links import link_errors  # pylint: disable=import-outside-toplevel; avoids a circular import
        if not self.course_id:
            return
        self.value = (self.value or '').strip().lower()
        for field in self.UTM_EXTRA_FIELDS:
            setattr(self, field, (getattr(self, field) or '').strip().lower())
        extras = {field: getattr(self, field) for field in self.UTM_EXTRA_FIELDS}
        errors = link_errors(self.kind, self.value, self.course, self.pk, extras, self.client_id)
        if errors:
            raise ValidationError(errors)


class CourseLinkDailyStat(models.Model):
    """
    Raw hits and unique visitors of a link for one day (Asia/Karachi date).
    """
    link = models.ForeignKey(CourseLink, on_delete=models.CASCADE, related_name='daily_stats')
    date = models.DateField()
    hits = models.PositiveIntegerField(default=0)
    unique_visitors = models.PositiveIntegerField(default=0)

    class Meta:
        app_label = 'pakx_admin_app'
        unique_together = ('link', 'date')
        ordering = ['-date']

    def __str__(self):
        return '{} {} hits={} unique={}'.format(self.link_id, self.date, self.hits, self.unique_visitors)


class CourseLinkVisitor(models.Model):
    """
    One row per (link, anonymous visitor id, day), used to count unique visitors per day and,
    by distinct visitor id, over a period. ``visitor_id`` is a random first-party cookie value;
    no IP address or user identity is stored.
    """
    link = models.ForeignKey(CourseLink, on_delete=models.CASCADE, related_name='visitors')
    visitor_id = models.CharField(max_length=32)
    date = models.DateField()

    class Meta:
        app_label = 'pakx_admin_app'
        # also serves the report's distinct-visitors query (link IN ... AND date BETWEEN ... COUNT(DISTINCT visitor_id))
        unique_together = ('link', 'date', 'visitor_id')
        indexes = [
            # retention pruning (date < cutoff)
            models.Index(fields=['date']),
        ]
