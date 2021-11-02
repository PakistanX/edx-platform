
def load_space_in_session(request):
    """
    Set partner space in Session, extracts from query params or sets pakx as default space
    """

    space_param = request.GET.get("space", None)
    active_space = request.session.get("space")
    if active_space is None and space_param is None:
        set_space_in_session(request, "pakx")  # Set Default space
    elif space_param:
        set_space_in_session(request, space_param)

    print("***********************\nLOADED SPACE\n*****************************")


def set_space_in_session(request, space):
    """
    sets given space in given request session
    """

    request.session["space"] = space
    print("***********************\nUPDATED SPACE\n*****************************")


def get_partner_space_meta(request):
    """
    get meta related to partner space
    """

    active_space = request.session.get("space")
    theme_class = "" if active_space == "pakx" else active_space
    return {
        'theme_class': theme_class
    }
