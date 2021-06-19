from organizations.models import Organization


def get_user_org_filter(user):
    organization = Organization.objects.get(user_profiles__user=user)
    return {'profile__organization': organization}
