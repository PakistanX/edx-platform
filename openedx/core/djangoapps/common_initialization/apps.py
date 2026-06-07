"""
Common initialization app for the LMS and CMS
"""


from django.apps import AppConfig


class CommonInitializationConfig(AppConfig):
    name = 'openedx.core.djangoapps.common_initialization'
    verbose_name = 'Common Initialization'

    def ready(self):
        # Common settings validations for the LMS and CMS.
        from . import checks
        from .ora_file_download import patch_ora_file_download_urls

        self._add_mimetypes()
        patch_ora_file_download_urls()

    @staticmethod
    def _add_mimetypes():
        """
        Add extra mimetypes. Used in xblock_resource.
        """
        import mimetypes

        mimetypes.add_type('application/vnd.ms-fontobject', '.eot')
        mimetypes.add_type('application/x-font-opentype', '.otf')
        mimetypes.add_type('application/x-font-ttf', '.ttf')
        mimetypes.add_type('application/font-woff', '.woff')
        mimetypes.add_type('application/x-pbix', '.pbix')
