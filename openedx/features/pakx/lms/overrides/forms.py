from django import forms
from django.forms import ModelForm
from django.utils.translation import ugettext as _

from openedx.features.pakx.lms.overrides.utils import validate_text_for_emoji

from .models import ContactUs
from .utils import get_phone_validators


class AboutUsForm(ModelForm):
    phone = forms.CharField(validators=get_phone_validators(), label='Phone',
                            help_text=_('+924235608000 or +442071838750'))

    class Meta:
        model = ContactUs
        fields = ('full_name', 'email', 'organization', 'phone', 'message')
        error_messages = {
            'email': {
                'invalid': _('Invalid email format. Email should be like joey@ilmx.com'),
            },
        }
        help_texts = {
            'email': _('user@email.com'),
            'message': _(''),
            'full_name': _('John Doe'),
            'organization': _('ilmX'),
        }

    def __init__(self, *args, **kwargs):
        hidden_fields = kwargs.pop('hidden_fields', [])
        super(AboutUsForm, self).__init__(*args, **kwargs)
        self.fields['full_name'].label = _('Full Name')
        self.fields['message'].label = _('Maximum words (4000)')

        for key, field in self.fields.items():
            if field.required:
                field.label = field.label + '*'
            self.fields[key].widget.attrs.update({'class': 'form-control {}'.format(
                'hidden' if key in hidden_fields else ''), 'placeholder': field.help_text})

    def clean_organization(self):
        value = self.cleaned_data['organization']
        validate_text_for_emoji(value)
        return value
