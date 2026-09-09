from logging import getLogger

import waffle
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.deprecation import MiddlewareMixin

log = getLogger(__name__)

# Waffle switch gating the monthly last_login refresh (toggle at runtime in the
# Django admin under Waffle -> Switches; off unless the switch exists and is on).
REFRESH_LAST_LOGIN_MONTHLY_SWITCH = 'ilmx.refresh_last_login_monthly'


class XFrameOptionsSameOriginMiddleware(MiddlewareMixin):
    def process_view(self, request, view_func, view_args, view_kwargs):
        if 'scorm' in request.path:
            response = view_func(request, *view_args, **view_kwargs)
            response['X-Frame-Options'] = 'SAMEORIGIN'
            return response
        return None


class RefreshLastLoginOnMonthChangeMiddleware(MiddlewareMixin):
    """
    Keep MAU (Monthly Active Users) counts honest for learners on long-lived
    sessions.

    ``last_login`` only updates on an actual ``login()`` call, so a learner who
    signed in late last month and keeps browsing on a still-valid session cookie
    never re-authenticates -- their ``last_login`` stays in the old month and the
    MAU report (which keys on ``last_login`` within the month) undercounts them.

    On the first authenticated request a user makes in a new calendar month
    (compared in the report's timezone), this bumps their ``last_login`` to now.
    After that the condition is false for the rest of the month, so it writes at
    most once per user per month.

    Notes:
    - Uses a targeted ``.update()`` (not ``user.save()``) so it is a single cheap
      UPDATE and does not fire the ``user_logged_in`` signal / login side effects.
    - Only ever advances ``last_login`` for users who actually made a request, so
      dormant-account handling (which keys on an old ``last_login``) is unaffected.
    - Gated by the ``ilmx.refresh_last_login_monthly`` waffle switch (default off)
      so it can be toggled at runtime and compared against current numbers before
      adopting.
    - Best-effort: any failure is logged and swallowed; it never breaks a request.
    """

    def process_request(self, request):
        if not waffle.switch_is_active(REFRESH_LAST_LOGIN_MONTHLY_SWITCH):
            return None
        try:
            user = getattr(request, 'user', None)
            if user is None or not user.is_authenticated:
                return None

            now = timezone.now()
            now_local = timezone.localtime(now)
            last = user.last_login
            if last is not None:
                last_local = timezone.localtime(last)
                if (last_local.year, last_local.month) >= (now_local.year, now_local.month):
                    return None  # already counts in the current month

            get_user_model().objects.filter(pk=user.pk).update(last_login=now)
            user.last_login = now  # keep request.user consistent for this request
        except Exception:  # pylint: disable=broad-except
            log.exception('RefreshLastLoginOnMonthChangeMiddleware failed')
        return None
