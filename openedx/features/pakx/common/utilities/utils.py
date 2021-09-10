

def get_whitelist_orgs_for_user(user):
    """
    Get list of whitelisted orgs whose courses user can access
    :param user: User object


    :return: str of args separated with space i.e "pakx" or "pakx arbisoft"
    """

    default_org = "pakx"  # pakx is default public org, every user has access to its courses

    user_org = getattr(user.profile.organization, "short_name", "")

    return "{} {}".format(default_org, user_org).strip().lower()

