from logging import getLogger

from django.conf import settings

from openedx.features.pakx.cms.custom_settings.models import PartnerSpace

log = getLogger(__name__)


def spaces_enabled():
    """ check if space is via LMS Configs """
    return settings.ENABLE_PARTNER_SPACES


def get_space_dropdown_options(request):
    """
    get space dropdown option list
    :param request: (HttpRequest) request object

    :returns: (list) list of any space is activated else None for default space
    """

    if is_default_space_activated(request):
        return None
    return [request.session.get("space_name"), 'ilmX']


def get_active_partner_space(request):
    """
    get active partner space form given request object
    """

    return request.session.get("space")


def is_default_space_activated(request):

    return get_active_partner_space(request) == settings.DEFAULT_PUBLIC_PARTNER_SPACE


def get_space_name_for_footer(request):
    """ Get partner space name for footer """
    if spaces_enabled():
        return request.session.get("space_name")
    return 'ilmX'


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

    # if Spaces flow is Disabled, ACTIVATE Default Space
    space = PartnerSpace.get_partner_space(space if spaces_enabled() else settings.DEFAULT_PUBLIC_PARTNER_SPACE)
    if space is None:
        raise Exception("No Partner space found, add a space by visiting "
                        "{}/admin/custom_settings/partnerspace/".format(request.get_host()))
    request.session["space_name"] = space.name  # Store exact styled name for footer
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


def get_login_page_links(request):
    """
    get login page links dict
    """

    active_space = get_active_partner_space(request)
    space_model = PartnerSpace.get_partner_space(active_space)

    links = {
        'active_space': active_space,
        'footer_links': space_model.footer_links,
        'partner_meta': space_model.partner_meta,
        'organization': space_model.organization.name
    }
    links.update(get_partner_space_meta(request))
    return links


def get_active_partner_model(request):
    """
    get active partner model
    """

    active_space = get_active_partner_space(request)
    return PartnerSpace.get_partner_space(active_space)
