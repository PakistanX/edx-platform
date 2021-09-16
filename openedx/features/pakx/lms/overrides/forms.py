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
