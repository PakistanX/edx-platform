# Generated by Django 2.2.16 on 2021-09-21 09:02

import django.core.validators
from django.db import migrations, models
import openedx.features.pakx.lms.overrides.utils


class Migration(migrations.Migration):

    dependencies = [
        ('overrides', '0005_contactus'),
    ]

    operations = [
        migrations.AlterField(
            model_name='contactus',
            name='full_name',
            field=models.CharField(max_length=24, validators=[openedx.features.pakx.lms.overrides.utils.validate_text_for_emoji, django.core.validators.MinLengthValidator(limit_value=3, message='Name should be of minimum 3 chars.'), django.core.validators.RegexValidator(message='Name can only contain alphabets.', regex='^[a-zA-Z ]*$')]),
        ),
        migrations.AlterField(
            model_name='contactus',
            name='message',
            field=models.TextField(validators=[django.core.validators.MaxLengthValidator(4000, message='Message should be of maximum 4000 chars.'), openedx.features.pakx.lms.overrides.utils.validate_text_for_emoji], verbose_name='How can we help you?'),
        ),
        migrations.AlterField(
            model_name='contactus',
            name='organization',
            field=models.CharField(blank=True, max_length=40, null=True, validators=[openedx.features.pakx.lms.overrides.utils.validate_text_for_emoji]),
        ),
        migrations.AlterField(
            model_name='contactus',
            name='phone',
            field=models.CharField(max_length=16, validators=[django.core.validators.MinLengthValidator(limit_value=11, message='Phone number should be of minimum 11 chars.'), django.core.validators.RegexValidator(message='Phone number can only contain numbers.', regex='^\\+?1?\\d*$')]),
        ),
    ]
