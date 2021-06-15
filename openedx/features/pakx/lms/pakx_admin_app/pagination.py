from rest_framework.pagination import PageNumberPagination


class PakxAdminAppPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'


class CourseEnrollmentPagination(PageNumberPagination):
    page_size = 3
    page_size_query_param = 'page_size'

    def get_paginated_response(self, data):
        response = super(CourseEnrollmentPagination, self).get_paginated_response(data)
        response.data['total_pages'] = self.page.paginator.num_pages
        return response
