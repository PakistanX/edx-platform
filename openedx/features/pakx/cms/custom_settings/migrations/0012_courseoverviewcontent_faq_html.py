# Generated by Django 2.2.16 on 2022-09-30 12:10

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('custom_settings', '0011_auto_20220930_1053'),
    ]

    operations = [
        migrations.AddField(
            model_name='courseoverviewcontent',
            name='faq_html',
            field=models.TextField(blank=True, default=''),
        ),
    ]
