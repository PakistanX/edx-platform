# Generated by Django 2.2.16 on 2022-05-23 14:32

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('course_modes', '0013_auto_20200115_2022'),
    ]

    operations = [
        migrations.AlterField(
            model_name='coursemode',
            name='currency',
            field=models.CharField(default='pkr', max_length=8),
        ),
        migrations.AlterField(
            model_name='coursemodesarchive',
            name='currency',
            field=models.CharField(default='pkr', max_length=8),
        ),
        migrations.AlterField(
            model_name='historicalcoursemode',
            name='currency',
            field=models.CharField(default='pkr', max_length=8),
        ),
    ]
