from django.conf import settings
from rest_framework.authentication import BaseAuthentication


class DiscoverAuthentication(authentication.BaseAuthentication):
    """Authenticate if request is from our discovery site on wordpress."""

    def authenticate(self, request):
        """Check if domain name in header is valid."""
        sender_domain = request.META.get("HTTP_ORIGIN", '')
        if sender_domain != settings.DISCOVER_URL:
            raise exceptions.AuthenticationFailed
