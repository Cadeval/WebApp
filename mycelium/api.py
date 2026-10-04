from typing import Annotated
import logging

from django.contrib.auth import aauthenticate, alogin, alogout
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django_bolt import AllowAny, BoltAPI
from shared.request_logging import configure_api_logging
from django_bolt.params import Form

# Authentication uses Django sessions; browser handlers apply their access guards.
api = BoltAPI(django_middleware=True)
configure_api_logging(api)
logger = logging.getLogger('cadevil.security')

# Session-authenticated browser pages share the same full/fragment renderer.
from django.contrib.auth.decorators import login_required
from shared.bolt_pages import page_endpoint
from shared.page_views import render_page

@api.get('/', guards=[AllowAny()])
@page_endpoint
def index(request):
    context = {}
    if not request.user.is_authenticated:
        from plugin_manager.django_resources import public_overview_templates

        context['landing_overview_templates'] = public_overview_templates()
    return render_page(request, 'index.jinja2', context)

@api.get('/mycelium/login', guards=[AllowAny()])
@page_endpoint
def login_page(request):
    if request.user.is_authenticated:
        return HttpResponseRedirect('/')
    return render_page(request, 'login.jinja2')

@api.post("/mycelium/login", guards=[AllowAny()])
async def login(
    request: HttpRequest,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
):
    request.META['CADEVIL_LOG_ROUTE'] = 'mycelium.api.login'
    user = await aauthenticate(request=request, username=username, password=password)
    if user:
        await alogin(request=request, user=user)
        logger.info('Session login succeeded', extra={'event': 'session_login', 'outcome': 'accepted'})
        response = HttpResponse(status=200)
        response["HX-Redirect"] = "/"
        return response
    else:
        logger.warning('Session login rejected', extra={'event': 'session_login', 'outcome': 'rejected'})
        response = HttpResponse(status=401)
        response["HX-Redirect"] = "/mycelium/login"
        return response


@api.post("/mycelium/logout")
async def logout(request: HttpRequest):
    request.META['CADEVIL_LOG_ROUTE'] = 'mycelium.api.logout'
    await alogout(request)
    logger.info('Session logout completed', extra={'event': 'session_logout', 'outcome': 'completed'})
    return HttpResponseRedirect(redirect_to="/")


@api.get('/mycelium/user', guards=[AllowAny()])
@api.get('/mycelium/profile', guards=[AllowAny()])
@api.get('/mycelium/user/profile', guards=[AllowAny()])
@page_endpoint
@login_required(login_url='/mycelium/login')
def user_view(request):
    return HttpResponseRedirect('/mycelium/settings')


from .settings_views import user_settings
from .user_admin import admin_users, admin_groups


@api.get('/mycelium/settings', name='user_settings', guards=[AllowAny()])
@api.post('/mycelium/settings', name='user_settings_save', guards=[AllowAny()])
@page_endpoint
def account_settings(request):
    return user_settings(request)


@api.get('/mycelium/settings/users', name='admin_users', guards=[AllowAny()])
@api.post('/mycelium/settings/users', name='admin_users_save', guards=[AllowAny()])
@page_endpoint
def user_management(request):
    return admin_users(request)


@api.get('/mycelium/settings/groups', name='admin_groups', guards=[AllowAny()])
@api.post('/mycelium/settings/groups', name='admin_groups_save', guards=[AllowAny()])
@page_endpoint
def group_management(request):
    return admin_groups(request)


@api.get('/mycelium/settings/teams',name='user_teams',guards=[AllowAny()])
@api.post('/mycelium/settings/teams',name='user_teams_save',guards=[AllowAny()])
@page_endpoint
def team_settings(request):
    from plugin_manager.teams import teams_page
    return teams_page(request)

@api.get('/demo', guards=[AllowAny()])
@page_endpoint
def recorded_demo(request):
    response = render_page(request, 'bim/demo.html', {'public_demo': True})
    return response
