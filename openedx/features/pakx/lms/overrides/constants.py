TEACHING_PRACTICE_COURSE_IDS = [
    'course-v1:LUMSx+2+2022',
    'course-v1:Lecole_AES+2+2023',
    'course-v1:LGS_JT+2+2022',
    'course-v1:Jadid_Dastgir+2+2022',
    'course-v1:Happy_Palace_Group_of_Schools.+2+2024',
    'course-v1:Farooqi_School+2+2024',
]

LUMS_CERTIFICATE_ORGANIZATIONS = [
    'LUMSx',
    'Happy_Home',
    'TCF',
    'Jadid_Dastgir',
    'Happy_Palace_Group_of_Schools.',
    'Farooqi_School',
    'Lumiere_Learnings_PVT_Ltd_KGS',
    'Risk_Associates',
    'Amal_Academy',
]

COURSE_SLUG_MAPPING = {
    '5e-model-teacher-training': 'course-v1:LUMSx+2+2022',
    'ayurveda-health-wellness': 'course-v1:LUMSx+10+2023',
    'curriculum-development': 'course-v1:LUMSx+3+2023',
    'learning-how-to-learn-urdu': 'course-v1:LUMSx+1+2022',
    'learn-farsi-for-beginners': 'course-v1:LUMSx+4+2022',
    'learn-pashto-for-beginners': 'course-v1:LUMSx+9+2023',
    'machine-learning': 'course-v1:LUMSx+6+2024',
    'project-management': 'course-v1:LUMSx+5+2023',
    'business-communication': 'course-v1:LUMSx+8+2023',
    'artificial-intelligence-classroom': 'course-v1:LUMSx+11+2024',
    'introduction-to-data-science': 'course-v1:LUMSx+12+2024',
    'art-of-persuasion': 'course-v1:LUMSx+13+2024',
}

TRAINING_SLUG_MAPPING = {
    'legal-framework-for-trade-pakistan': 'course-v1:PSW+TL02+2023',
    'customs-tariff-essentials': 'course-v1:PSW+TL01+2023',
    'export-goods-services-pakistan': 'course-v1:PSW+TL03+2023',
    'digital-transformation-trade-pakistan': 'course-v1:PSW+TL04+2023',
    'fundamentals-of-international-trade': 'course-v1:PSW+TL05+2023',
}

from django.conf import settings
FONT_MAP = {
    "Helvetica": "{}/pakx/fonts/Helvetica.ttf".format(settings.STATIC_ROOT_BASE),
    "Helvetica-Bold": "{}/pakx/fonts/Helvetica-Bold.ttf".format(settings.STATIC_ROOT_BASE),
    "Century-Gothic": "{}/pakx/fonts/CenturyGothicPaneuropeanRegular.ttf".format(settings.STATIC_ROOT_BASE),
    "Poppins-Bold": "{}/pakx/fonts/Poppins-Bold.ttf".format(settings.STATIC_ROOT_BASE)
}

CERTIFICATE_LAYOUT_CONFIGS_DEFAULT = {
    'certificate_date_issued': {
        "position": [319, 720],
        "font_size": 10,
        "box_size": [700, 57],
        "font": "Helvetica"
    },
    'accomplishment_copy_name': {
        "position": [316, 871],
        "font_size": 15,
        "box_size": [1000, 77],
        "font": "Helvetica-Bold",
        "transform": "upper"
    },
    'certificate_id_number': {
        "position": [1276, 2338],
        "font_size": 8,
        "box_size": [1000, 47],
        "font": "Century-Gothic",
        "prefix": "Valid Certificate ID: "
    }
}
