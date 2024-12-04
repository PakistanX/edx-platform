from django.utils.deprecation import MiddlewareMixin


class XFrameOptionsSameOriginMiddleware(MiddlewareMixin):
    def process_view(self, request, view_func, view_args, view_kwargs):
        if 'scorm' in request.path:
            response = view_func(request, *view_args, **view_kwargs)
            response['X-Frame-Options'] = 'SAMEORIGIN'
            return response
        return None
