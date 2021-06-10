from django.contrib.auth.models import Group
from django.contrib.auth.tokens import default_token_generator
from django.contrib.sites.models import Site
from django.conf import settings
from django.db.models.query_utils import Q
from django.urls import reverse
from django.utils.http import int_to_base36
from edx_ace import ace
from edx_ace.recipient import Recipient

from openedx.core.djangoapps.ace_common.template_context import get_base_template_context
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers

from .constants import GROUP_TRAINING_MANAGERS, ADMIN, STAFF, TRAINING_MANAGER, LEARNER
from .message_types import RegistrationNotification


def get_roles_q_filters(roles):
    qs = Q()

    for role in roles:
        if int(role) == ADMIN:
            qs |= Q(is_superuser=True)
        elif int(role) == STAFF:
            qs |= Q(is_staff=True)
        elif int(role) == LEARNER:
            qs |= ~Q(Q(is_superuser=True) | Q(is_staff=True) | Q(groups__name=GROUP_TRAINING_MANAGERS))
        elif int(role) == TRAINING_MANAGER:
            qs |= Q(groups__name=GROUP_TRAINING_MANAGERS)

    return qs


def specify_user_role(user, role):
    if role == ADMIN:
        user.is_superuser = True
    elif role == STAFF:
        user.is_staff = True
    elif role == TRAINING_MANAGER:
        user.groups.add(Group.objects.get(name=GROUP_TRAINING_MANAGERS))
    user.save()


def get_email_message_context(user, user_profile, protocol):
    site = Site.objects.get_current()
    message_context = {
        'site_name': site.domain
    }
    message_context.update(get_base_template_context(site))
    message_context.update({
        'platform_name': configuration_helpers.get_value('PLATFORM_NAME', settings.PLATFORM_NAME),
        'firstname': user.first_name,
        'username': user.username,
        'email': user.email,
        'employee_id': user_profile.employee_id,
        'language': user_profile.language,
        'reset_password_link': '{protocol}://{site}{link}'.format(
            protocol=protocol,
            site=configuration_helpers.get_value('SITE_NAME', settings.SITE_NAME),
            link=reverse('password_reset_confirm', kwargs={
                'uidb36': int_to_base36(user.id),
                'token': default_token_generator.make_token(user),
            }),
        )
    })
    return message_context


def send_registration_email(user, user_profile, protocol):
    message = RegistrationNotification().personalize(
        recipient=Recipient(user.username, user.email),
        language=user_profile.language,
        user_context=get_email_message_context(user, user_profile, protocol),
    )
    ace.send(message)
