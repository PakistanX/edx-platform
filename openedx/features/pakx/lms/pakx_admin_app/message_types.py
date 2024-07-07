"""
Contains message types which will be used by edx ace.
"""
from openedx.core.djangoapps.ace_common.message import BaseMessageType


class RegistrationNotification(BaseMessageType):
    pass


class EnrolmentNotification(BaseMessageType):
    pass


class CourseReminder(BaseMessageType):
    pass


class CommerceEnrol(BaseMessageType):
    pass


class CommerceCODOrder(BaseMessageType):
    pass
