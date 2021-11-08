from logging import getLogger
from django.conf import settings
from openedx.features.pakx.cms.custom_settings.models import PartnerSpace

log = getLogger(__name__)


def get_active_partner_space(request):
    """
    get active partner space form given request object
    """

    return request.session.get("space")


def load_space_in_session(request):
    """
    Set partner space in Session, extracts from query params or sets pakx as default space
    """

    space_param = request.GET.get("space", None)
    active_space = get_active_partner_space(request)
    if active_space is None and space_param is None:
        set_partner_space_in_session(request, settings.DEFAULT_PUBLIC_PARTNER_SPACE)  # Set Default space
    elif space_param:
        set_partner_space_in_session(request, space_param)


def set_partner_space_in_session(request, space):
    """
    sets given space in given request session
    """

    space = PartnerSpace.get_partner_space(space)
    if space is None:
        raise Exception("No Partner space found, add a space by visiting <lms>/admin/custom_settings/partnerspace/")

    request.session["space"] = space.name.lower()


def get_partner_space_meta(request):
    """
    get meta related to partner space
    """

    active_space = get_active_partner_space(request)
    theme_class = "" if active_space == settings.DEFAULT_PUBLIC_PARTNER_SPACE else active_space
    return {
        'theme_class': theme_class
    }


def get_active_partner_model(request):
    """
    get active partner model
    """

    active_space = get_active_partner_space(request)
    return PartnerSpace.get_partner_space(active_space)
