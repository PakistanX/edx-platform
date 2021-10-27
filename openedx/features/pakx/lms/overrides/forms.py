from django import forms
from django.forms import ModelForm
from django.utils.translation import ugettext as _

from openedx.features.pakx.lms.overrides.utils import validate_text_for_emoji

from .models import ContactUs
from .utils import get_phone_validators


class AboutUsForm(ModelForm):
    phone = forms.CharField(validators=get_phone_validators(), label='Phone', help_text=_('04235608000 or 03317758391'))

    class Meta:
        model = ContactUs
        fields = ('full_name', 'email', 'organization', 'phone', 'message')
        error_messages = {
            'email': {
                'invalid': _('Invalid email format. Email should be like joey@pakistanx.com'),
            },
        }
        help_texts = {
            'email': _('user@website.com'),
            'message': _('Maximum words (4000)'),
        }

    def __init__(self, *args, **kwargs):
        super(AboutUsForm, self).__init__(*args, **kwargs)
        self.fields['full_name'].label = _('Full Name')
        for key, field in self.fields.items():
            if field.required:
                field.label = field.label + '*'
            self.fields[key].widget.attrs.update({'class': 'form-control', 'placeholder': field.label})

    def clean_organization(self):
        value = self.cleaned_data['organization']
        validate_text_for_emoji(value)
        return value
