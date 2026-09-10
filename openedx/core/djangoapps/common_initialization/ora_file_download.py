"""
Monkey patch ORA2 S3 file download URLs so presigned links include the
original submission filename and content type from submissions_submission.answer.
"""

from __future__ import absolute_import, unicode_literals

import json
import logging
import mimetypes
import os

import six

from openassessment.fileupload.exceptions import FileUploadInternalError

logger = logging.getLogger(__name__)

_PATCH_APPLIED = False

# Extensions that Python's mimetypes module may not recognize reliably.
EXTRA_CONTENT_TYPES_BY_EXTENSION = {
    '.pbix': 'application/x-pbix',
}


def _ensure_extra_mimetypes():
    """
    Register non-standard MIME types used by ORA custom file uploads.
    """
    for extension, content_type in EXTRA_CONTENT_TYPES_BY_EXTENSION.items():
        mimetypes.add_type(content_type, extension)


def _guess_content_type(file_name):
    """
    Guess the response Content-Type for a downloaded ORA file.

    Uses explicit extension mappings first so uncommon types such as .pbix
    resolve correctly even when mimetypes is not fully initialized.
    """
    _, extension = os.path.splitext(six.text_type(file_name))
    extension_lower = extension.lower()
    if extension_lower in EXTRA_CONTENT_TYPES_BY_EXTENSION:
        return EXTRA_CONTENT_TYPES_BY_EXTENSION[extension_lower]

    content_type, _encoding = mimetypes.guess_type(file_name)
    return content_type or 'application/octet-stream'


def _parse_file_key(key):
    """
    Parse an ORA file upload key into its components.

    Keys are formatted as ``student_id/course_id/item_id`` or
    ``student_id/course_id/item_id/<index>``.
    """
    parts = key.split('/')
    if len(parts) < 3:
        return None

    file_index = 0
    if len(parts) > 3:
        try:
            file_index = int(parts[3])
        except (TypeError, ValueError):
            return None

    return {
        'student_id': parts[0],
        'course_id': parts[1],
        'item_id': parts[2],
        'file_index': file_index,
    }


def _extract_file_name_from_answer(answer, key, file_index):
    """
    Return the uploaded file name stored in a submission answer for ``key``.
    """
    if 'file_keys' in answer:
        file_keys = answer.get('file_keys') or []
        file_names = answer.get('files_name') or answer.get('files_names') or []

        if key in file_keys:
            idx = file_keys.index(key)
        elif file_index < len(file_keys):
            idx = file_index
        else:
            return None

        if idx >= len(file_names) or not file_names[idx]:
            return None

        return six.text_type(file_names[idx]).strip()

    if answer.get('file_key') == key:
        file_name = answer.get('file_name') or ''
        return six.text_type(file_name).strip() or None

    return None


def _parse_submission_answer(submission):
    """
    Return the parsed submission answer payload.

    edx-submissions stores answers on Submission.answer (JSONField backed by
    the raw_answer DB column). Older callers may still expose raw_answer.
    """
    answer = getattr(submission, 'answer', None)
    if answer is None:
        answer = getattr(submission, 'raw_answer', None)
    if answer is None:
        return None

    if isinstance(answer, dict):
        return answer

    if isinstance(answer, six.string_types):
        try:
            answer = json.loads(answer)
        except (TypeError, ValueError):
            return None
        return answer if isinstance(answer, dict) else None

    return None


def _get_file_metadata_from_submission(key):
    """
    Look up the original filename and MIME type for an ORA upload key.

    Returns:
        tuple: (file_name, content_type) or (None, None) if unavailable.
    """
    key_parts = _parse_file_key(key)
    if not key_parts:
        return None, None

    # Import lazily so Django apps are loaded before model access.
    from submissions.models import StudentItem, Submission

    try:
        student_item = StudentItem.objects.get(
            student_id=key_parts['student_id'],
            course_id=key_parts['course_id'],
            item_id=key_parts['item_id'],
        )
    except StudentItem.DoesNotExist:
        return None, None

    submissions = Submission.objects.filter(
        student_item=student_item,
    ).order_by('-attempt_number')

    for submission in submissions:
        answer = _parse_submission_answer(submission)
        if not answer:
            continue

        file_name = _extract_file_name_from_answer(
            answer,
            key,
            key_parts['file_index'],
        )
        if file_name:
            return file_name, _guess_content_type(file_name)

    return None, None


def _build_content_disposition(file_name):
    """
    Build a Content-Disposition header value for S3 presigned URLs.
    """
    safe_name = (
        six.text_type(file_name)
        .replace('\\', '\\\\')
        .replace('"', '\\"')
    )
    return u'attachment; filename="{}"'.format(safe_name)


def _make_patched_s3_get_download_url(original_get_download_url):
    """
    Wrap the ORA2 S3 backend download URL generator.
    """
    def patched_get_download_url(self, key):
        bucket_name, key_name = self._retrieve_parameters(key)
        file_name, content_type = None, None
        try:
            file_name, content_type = _get_file_metadata_from_submission(key)
        except Exception:
            logger.exception(
                u'Failed to resolve ORA file metadata for key %s; falling back to default download URL.',
                key,
            )

        try:
            from openassessment.fileupload.backends.s3 import _connect_to_s3

            conn = _connect_to_s3()
            bucket = conn.get_bucket(bucket_name)
            s3_key = bucket.get_key(key_name)
            if not s3_key:
                return ""

            generate_url_kwargs = {
                'expires_in': self.DOWNLOAD_URL_TIMEOUT,
            }
            if file_name and content_type:
                generate_url_kwargs['response_headers'] = {
                    'response-content-disposition': _build_content_disposition(file_name),
                    'response-content-type': content_type,
                }

            return s3_key.generate_url(**generate_url_kwargs)
        except Exception as ex:
            logger.exception(
                u"An internal exception occurred while generating a download URL."
            )
            raise FileUploadInternalError(ex)

    patched_get_download_url.__name__ = 'get_download_url'
    patched_get_download_url.__doc__ = original_get_download_url.__doc__
    return patched_get_download_url


def patch_ora_file_download_urls():
    """
    Apply the ORA2 S3 download URL monkey patch once.
    """
    global _PATCH_APPLIED  # pylint: disable=global-statement
    if _PATCH_APPLIED:
        return

    _ensure_extra_mimetypes()

    try:
        from openassessment.fileupload.backends import s3 as s3_backend
    except ImportError:
        logger.warning(
            'openassessment is not installed; skipping ORA file download URL patch.'
        )
        return

    original_get_download_url = s3_backend.Backend.get_download_url
    s3_backend.Backend.get_download_url = _make_patched_s3_get_download_url(
        original_get_download_url,
    )
    _PATCH_APPLIED = True
