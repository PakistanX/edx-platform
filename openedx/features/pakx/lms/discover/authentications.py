import logging

from django.conf import settings
from rest_framework import authentication, exceptions

logger = logging.getLogger(__name__)


class DiscoverAuthentication(authentication.BaseAuthentication):
    """Authenticate if request is from our discovery site on wordpress."""

    def authenticate(self, request):
        """Check if domain name in header is valid."""
        sender_domain = request.META.get("HTTP_ORIGIN", '')

        logger.info('\n\n\n\n{}\n\n{}\n\n\n\n'.format(sender_domain, settings.DISCOVER_URL))

        if sender_domain != settings.DISCOVER_URL:
            raise exceptions.AuthenticationFailed
