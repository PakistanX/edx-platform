from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pakx_admin_app', '0002_auto_20210616_1624'),
    ]

    operations = [
        migrations.CreateModel(
            name='MAUReport',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('organization_short_name', models.CharField(
                    blank=True, default='', max_length=255,
                    help_text="Org short_name this report covers; blank for the overall (all-orgs) report.")),
                ('organization_name', models.CharField(blank=True, default='', max_length=255)),
                ('is_overall', models.BooleanField(default=False)),
                ('month', models.DateField(help_text='First day of the reported month.')),
                ('active_user_count', models.PositiveIntegerField(default=0)),
                ('excluded_staff_count', models.PositiveIntegerField(default=0)),
                ('file_path', models.CharField(max_length=500)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['-month', 'organization_name'],
                'unique_together': {('organization_short_name', 'is_overall', 'month')},
            },
        ),
        migrations.CreateModel(
            name='MAUReportExcludedUser',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('organization_short_name', models.CharField(
                    blank=True, default='', max_length=255,
                    help_text="Org short_name; leave blank to exclude the username from every org's report.")),
                ('username', models.CharField(max_length=150)),
            ],
            options={
                'unique_together': {('organization_short_name', 'username')},
            },
        ),
    ]
