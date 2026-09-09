"""
Models for the PakX Admin Panel app.
"""
from django.db import models


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
