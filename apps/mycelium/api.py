from asgiref.sync import sync_to_async
from typing import Annotated

from django.contrib.auth import aauthenticate, alogin, alogout
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.views.decorators.vary import vary_on_headers
from django_bolt import AllowAny, BoltAPI
from django_bolt.params import Form

# `auth`/`guards` are left unset on most routes below so they inherit the
# project-wide defaults configured in settings (`BOLT_AUTHENTICATION_CLASSES`
# / `BOLT_DEFAULT_PERMISSION_CLASSES`, see config/settings/base.py) — JWT
# authentication + "must be authenticated". Routes that need to stay public
# (login) or need a different, fine-grained permission (the MCP mount) opt
# out explicitly with `guards=[...]`.
api = BoltAPI(django_middleware=True)

# The JWT lives in an httponly cookie, so the browser sends it automatically
# on every request (see JWTAuthentication(cookie="access_token") in the
# global BOLT_AUTHENTICATION_CLASSES). One hour matches the token's `exp`.
ACCESS_TOKEN_MAX_AGE = 3600


# Session-authenticated browser pages share the same full/fragment renderer.
from django.contrib.auth.decorators import login_required
from apps.shared.bolt_pages import page_endpoint
from apps.shared.page_views import render_page

@api.get('/', guards=[AllowAny()])
@page_endpoint
def index(request):
    return render_page(request, 'index.jinja2')

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
    user = await aauthenticate(request=request, username=username, password=password)
    # user = await CadevilUser.objects.filter(username=username).afirst()
    if user:
        await alogin(request=request, user=user)
        response = HttpResponse(status=200)
        response["HX-Redirect"] = "/"
        return response
        # await alogin(request, user)
        # return {"status": "logged in", "username": user.username}
    else:
        response = HttpResponse(status=401)
        response["HX-Redirect"] = "/mycelium/login"
        return response


@api.post("/mycelium/logout")
async def logout(request: HttpRequest):
    user = await request.auser()
    if user.is_authenticated:
        print(user)
        await alogout(request)
    else:
        print(f"Second call {user}")

    # FIXME: No redirect on HX-Redirect Header when logging out.
    #  Might be because we are missing the route on GET?
    #  We shall use the HttpResponseRedirect
    response = HttpResponseRedirect(redirect_to="/")

    # response = HttpResponse(status=200)
    # response["HX-Request"] = "true"
    # response["HX-Redirect"] = "/"
    return response
    # response.delete_cookie("access_token")


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

@api.get('/demo', guards=[AllowAny()])
@page_endpoint
def recorded_demo(request):
    response = render_page(request, 'bim/demo.html', {'public_demo': True})
    return response
