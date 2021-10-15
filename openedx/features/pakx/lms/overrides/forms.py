from django import forms
from django.core.validators import ValidationError
from django.forms import ModelForm
from django.utils.translation import ugettext as _

from openedx.features.pakx.lms.overrides.utils import validate_text_for_emoji

from .models import ContactUs


class AboutUsForm(ModelForm):
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
            'phone': _('04235608000 or 03317758391'),
            'message': _('Maximum words (4000)'),
        }

    def __init__(self, *args, **kwargs):
        super(AboutUsForm, self).__init__(*args, **kwargs)
        for key, field in self.fields.items():
            if field.required:
                field.label = field.label + '*'
            self.fields[key].widget.attrs.update({'class': 'form-control', 'placeholder': field.label})

    def clean_organization(self):
        value = self.cleaned_data['organization']
        validate_text_for_emoji(value)
        return value


class MarketingForm(AboutUsForm):
    phone = forms.CharField(required=False, label='Phone')
    organization = forms.CharField(required=False, label='Organization')

    class Meta(AboutUsForm.Meta):
        fields = ('full_name', 'organization', 'email', 'phone', 'message')

    def __init__(self, *args, **kwargs):
        super(MarketingForm, self).__init__(*args, **kwargs)
        self.fields['organization'].help_text = _('Organization')
        self.fields['phone'].label = _('04235608000 or 03317758391')
        for key, field in self.fields.items():
            attr = {'class': 'form-control',
                    'placeholder': "{}*".format(field.help_text) if field.help_text else field.label}
            self.fields[key].widget.attrs.update(attr)

    def clean_organization(self):
        org = super().clean_organization()

        if org is None or org.strip() == '':
            raise ValidationError("Organization is required")
        return org
