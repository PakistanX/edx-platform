import re

from django.core.exceptions import ValidationError
from django.forms import ModelForm

from .models import ContactUs


class ContactUsForm(ModelForm):
    class Meta:
        model = ContactUs
        exclude = ('created_by',)

    def __init__(self, *args, **kwargs):
        super(ContactUsForm, self).__init__(*args, **kwargs)
        for key, field in self.fields.items():
            self.fields[key].widget.attrs.update({'class': 'form-control', 'placeholder': field.label})

    def clean_phone(self):
        if not self.cleaned_data['phone']:
            raise ValidationError('phone number is required!')
        if not re.match('^[\\s0-9+]+$', self.cleaned_data['phone']):
            raise ValidationError('Invalid phone number!')
        return self.cleaned_data['phone'].strip()
