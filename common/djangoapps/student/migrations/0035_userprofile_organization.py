# Generated by Django 2.2.16 on 2021-06-17 08:28

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('organizations', '0001_squashed_0007_historicalorganization'),
        ('student', '0034_userprofile_employee_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='organization',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='user_profiles', to='organizations.Organization'),
        ),
    ]
