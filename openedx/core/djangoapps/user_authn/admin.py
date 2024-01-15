from csv import writer as csv_writer
from datetime import datetime

from django.contrib import admin
from django.contrib.auth.models import User
from django.contrib.auth.admin import UserAdmin
from django.http import HttpResponse


def download_as_csv(model_admin, request, queryset):    # pylint: disable=unused-argument
    opts = model_admin.model._meta  # pylint: disable=protected-access
    content_disposition = 'attachment; filename={}.csv'.format(opts.verbose_name)
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = content_disposition
    writer = csv_writer(response)

    fields = [
        field for field in opts.get_fields() if (
            not field.many_to_many and not field.one_to_many and not field.one_to_one and field.name != 'password'
        )
    ]
    # Write a first row with header information
    writer.writerow([field.verbose_name for field in fields])
    # Write data rows
    for obj in queryset:
        data_row = []
        for field in fields:
            value = getattr(obj, field.name)
            if isinstance(value, datetime):
                value = value.strftime('%d/%m/%Y')
            data_row.append(value)
        writer.writerow(data_row)
    return response


download_as_csv.short_description = 'Download as CSV'


class CustomUserAdmin(UserAdmin):
    """
    Admin interface for User object
    """

    actions = [download_as_csv]


admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)
