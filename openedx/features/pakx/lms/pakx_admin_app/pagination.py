"""
module for custom pagination classes
"""
from rest_framework.pagination import PageNumberPagination

from .constants import MAU_REPORT_DEFAULT_PAGE_SIZE, MAU_REPORT_MAX_PAGE_SIZE


class PakxAdminAppPagination(PageNumberPagination):
    """
    Basic pagination for PakX Admin Panel
    """
    page_size = 10
    page_size_query_param = 'page_size'

    def get_paginated_response(self, data):
        response = super(PakxAdminAppPagination, self).get_paginated_response(data)

        if 'total_users_count' in response.data.get('results'):
            response.data['total_users_count'] = response.data['results']['total_users_count']
            response.data['results'] = response.data['results']['users']

        return response


class CourseEnrollmentPagination(PageNumberPagination):
    """
    Pagination class for course enrollment API
    """
    page_size = 3
    page_size_query_param = 'page_size'

    def get_paginated_response(self, data):
        response = super(CourseEnrollmentPagination, self).get_paginated_response(data)
        response.data['total_pages'] = self.page.paginator.num_pages
        return response


class MAUReportPagination(PageNumberPagination):
    """
    Pagination for the MAU reports table: default page size 10, client-adjustable
    via `page_size` up to a capped maximum.
    """
    page_size = MAU_REPORT_DEFAULT_PAGE_SIZE
    page_size_query_param = 'page_size'
    max_page_size = MAU_REPORT_MAX_PAGE_SIZE

    def get_paginated_response(self, data):
        response = super(MAUReportPagination, self).get_paginated_response(data)
        response.data['total_pages'] = self.page.paginator.num_pages
        return response
