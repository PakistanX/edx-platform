"""Management command to Create A default Partner space"""

from logging import getLogger

from django.core.management.base import BaseCommand

from openedx.features.pakx.cms.custom_settings.models import PartnerSpace

log = getLogger(__name__)


class Command(BaseCommand):
    """
    A management command that create a default Partner space that will act as public space for the platform
    """

    help = "Create a default Partner space that will act as public space for the platform"

    def handle(self, *args, **options):

        default_space = 'ilmx'
        from organizations.models import Organization
        ilmx_org, created = Organization.objects.get_or_create(name="ilmX", short_name=default_space, defaults={
            'active': True
        })

        PartnerSpace.objects.get_or_create(name=default_space, organization=ilmx_org)
        log.info("Created a public space named `ilmx` linked with ilmX org")
