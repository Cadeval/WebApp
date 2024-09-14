# debug_toolbar_check.py

from django.conf import settings
from django.core.exceptions import MiddlewareNotUsed
from django.test.client import RequestFactory
from debug_toolbar.middleware import DebugToolbarMiddleware

def check_debug_toolbar():
    print("Checking Django Debug Toolbar configuration...")

    # Check DEBUG setting
    print(f"DEBUG is set to: {settings.DEBUG}")

    # Check INTERNAL_IPS
    print(f"INTERNAL_IPS: {settings.INTERNAL_IPS}")

    # Check INSTALLED_APPS
    print("debug_toolbar in INSTALLED_APPS:", 'debug_toolbar' in settings.INSTALLED_APPS)

    # Check MIDDLEWARE
    print("DebugToolbarMiddleware in MIDDLEWARE:", 'debug_toolbar.middleware.DebugToolbarMiddleware' in settings.MIDDLEWARE)

    # Check if static files are properly configured
    print("django.contrib.staticfiles in INSTALLED_APPS:", 'django.contrib.staticfiles' in settings.INSTALLED_APPS)

    # Test the middleware
    try:
        factory = RequestFactory()
        request = factory.get('/')
        request.META['REMOTE_ADDR'] = '127.0.0.1'
        middleware = DebugToolbarMiddleware(lambda req: None)
        response = middleware(request)
        print("DebugToolbarMiddleware processed the request successfully")
    except MiddlewareNotUsed:
        print("DebugToolbarMiddleware is not used (this might be intentional)")
    except Exception as e:
        print(f"Error occurred while testing DebugToolbarMiddleware: {str(e)}")

    # Check DEBUG_TOOLBAR_CONFIG
    debug_toolbar_config = getattr(settings, 'DEBUG_TOOLBAR_CONFIG', {})
    print(f"DEBUG_TOOLBAR_CONFIG: {debug_toolbar_config}")

if __name__ == "__main__":
    check_debug_toolbar()