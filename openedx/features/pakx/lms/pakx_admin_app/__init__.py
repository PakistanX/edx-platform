# Activate PakxAdminAppConfig (Django 2.2 needs this since INSTALLED_APPS lists
# the module path, not the config) so its verbose_name is used in the admin.
default_app_config = 'openedx.features.pakx.lms.pakx_admin_app.apps.PakxAdminAppConfig'
