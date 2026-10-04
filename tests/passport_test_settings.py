"""Isolated calculation and web tests; never touch the user's runtime database."""
from pathlib import Path
BASE_DIR=Path(__file__).resolve().parent
SECRET_KEY='passport-tests-only'
DEBUG=False
ALLOWED_HOSTS=['testserver']
INSTALLED_APPS=['django.contrib.auth','django.contrib.contenttypes','django.contrib.sessions',
                'django.contrib.messages','django.contrib.admin','shared','plugin_manager']
AUTH_USER_MODEL='shared.CadevilUser'
DATABASES={'default':{'ENGINE':'django.db.backends.sqlite3','NAME':':memory:'}}
ROOT_URLCONF='tests.passport_test_urls'
MIDDLEWARE=['django.contrib.sessions.middleware.SessionMiddleware','django.middleware.common.CommonMiddleware',
            'django.middleware.csrf.CsrfViewMiddleware','django.contrib.auth.middleware.AuthenticationMiddleware',
            'django.contrib.messages.middleware.MessageMiddleware']
TEMPLATES=[{'BACKEND':'django.template.backends.django.DjangoTemplates','APP_DIRS':True,'DIRS':[Path(__file__).resolve().parent.parent/'resources/templates'],'OPTIONS':{
    'context_processors':['django.template.context_processors.request','django.contrib.auth.context_processors.auth',
                          'django.contrib.messages.context_processors.messages']}}]
USE_TZ=True
DEFAULT_AUTO_FIELD='django.db.models.BigAutoField'
PLUGIN_BUILTINS={'cadevil.bim.model_manager':'bim_model_manager:plugin_manifest'}

ADMIN_LOG_ENABLED=False

from plugin_manager.django_resources import resource_app_configs
INSTALLED_APPS += resource_app_configs(BASE_DIR.parent, {
    **PLUGIN_BUILTINS, "example_plugin": "example_plugin:plugin_manifest",
})
