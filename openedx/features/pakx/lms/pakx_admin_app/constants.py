"""
Constants for the need of Admin Panel
"""
GROUP_TRAINING_MANAGERS = 'Training Manager'
GROUP_ORGANIZATION_ADMIN = 'Organization Admins'
BULK_REGISTRATION_TASK_SUCCESS_MSG = 'Task has been started successfully. You will receive the stats email shortly.'
SELF_ACTIVE_STATUS_CHANGE_ERROR_MSG = "User can't change their own activation status."
ENROLLMENT_COURSE_EXPIRED_MSG = 'Enrollment date is passed for selected courses. ' \
                                'Refresh the page to get the updated course list.'
ENROLLMENT_COURSE_DIFF_ORG_ERROR_MSG = "Your organization does not match with selected course(s)."
ENROLLMENT_SUCCESS_MESSAGE = 'Enrollment task has been started successfully!\n' \
                             'Please refresh the page after couple of minutes to get the updated stats.'
USER_ACCOUNT_DEACTIVATED_MSG = 'This account has been deactivated.'
SELF_PASSWORD_RESET_ERROR_MSG = "User can't request password reset for their own account."

ORG_ADMIN = 1
STAFF = 2
TRAINING_MANAGER = 3
LEARNER = 4

ORG_ROLES = (
    (ORG_ADMIN, 'Admin'),
    (TRAINING_MANAGER, 'Training Manager'),
    (LEARNER, 'LEARNER'),
)
