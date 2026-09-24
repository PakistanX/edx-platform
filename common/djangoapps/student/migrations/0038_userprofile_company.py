from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('student', '0037_auto_20240903_0910'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='company',
            field=models.CharField(blank=True, default='', max_length=128),
        ),
    ]
