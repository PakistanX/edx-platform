# Generated by Django 2.2.16 on 2022-01-21 08:51

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('custom_settings', '0006_merge_20211129_1131'),
    ]

    operations = [
        migrations.AddField(
            model_name='courseoverviewcontent',
            name='publisher_description',
            field=models.TextField(blank=True, null=True),
        ),
    ]
