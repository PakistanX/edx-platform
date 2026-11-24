"""
ACE message types for Course progress emails.
"""


from openedx.core.djangoapps.ace_common.message import BaseMessageType

VERIFIED_UPGRADE_FROM_ADDRESS = 'support@ilmx.org'


class CourseProgress(BaseMessageType):
    def __init__(self, *args, **kwargs):
        super(CourseProgress, self).__init__(*args, **kwargs)

        self.options['transactional'] = True


class ContactUs(BaseMessageType):
    pass


class PostAssessment(BaseMessageType):
    pass


class VerifiedUpgradeCampaign(BaseMessageType):
    def __init__(self, *args, **kwargs):
        super(VerifiedUpgradeCampaign, self).__init__(*args, **kwargs)

        # Learners reply to this email with questions about the certificate, so it comes from support, not no-reply
        self.options['from_address'] = VERIFIED_UPGRADE_FROM_ADDRESS
