"""Team scopes never grant Django group permissions or expose private keys."""
import logging
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import JsonResponse, Http404
from django.shortcuts import get_object_or_404
from shared.page_views import render_page
from .models import Team, TeamMembership, PluginSigningKey

logger=logging.getLogger('cadevil.security')


def team_administrator(user, permission='change_team'):
    return bool(user and user.is_authenticated and user.is_active and user.is_staff and user.has_perm('plugin_manager.'+permission))


def member_of(user, team):
    return bool(user and user.is_authenticated and user.is_active and team.active and TeamMembership.objects.filter(team=team,user=user).exists())


def can_manage_team(user, team):
    return team_administrator(user) or bool(user and user.is_authenticated and user.is_active and team.active and user.has_perm('plugin_manager.change_team') and TeamMembership.objects.filter(team=team,user=user,role=TeamMembership.Role.MANAGER).exists())


def visible_teams(user):
    if not user or not user.is_authenticated or not user.is_active: return Team.objects.none()
    if team_administrator(user,'view_team'):
        return Team.objects.all()
    return Team.objects.filter(active=True,memberships__user=user).distinct()


def can_manage_key(user, key):
    if key.team_id:
        return can_manage_team(user,key.team)
    return bool(user.is_active and (key.owner_id==user.pk or (user.is_staff and user.has_perm('plugin_manager.change_pluginsigningkey'))))


def can_publish_key(user, key):
    if not user or not user.is_authenticated or not user.is_active:
        return False
    if key.team_id:
        return member_of(user,key.team)
    return key.owner_id==user.pk


def public_key_data(key):
    from .certificate_authority import certificate_chain
    return {'key_id':key.fingerprint,'label':key.label,'public_key':key.public_key,
            'scope':'team' if key.team_id else 'personal','team_id':str(key.team_id) if key.team_id else None,
            'revoked':key.revoked_at is not None,'certificate_chain':certificate_chain(key)}


@login_required(login_url='/mycelium/login')
def team_public_keys(request,team_id,key_id=None):
    try: team=get_object_or_404(Team,pk=team_id)
    except ValidationError as error: raise Http404('Unknown team.') from error
    if not member_of(request.user,team) and not team_administrator(request.user,'view_team'):
        raise PermissionDenied('Only current team members may read team public keys.')
    keys=PluginSigningKey.objects.filter(team=team).select_related('certificate_authority')
    data=public_key_data(get_object_or_404(keys,fingerprint=key_id)) if key_id else {'team_id':str(team.pk),'keys':[public_key_data(key) for key in keys]}
    response=JsonResponse(data);response['Cache-Control']='private, no-store';response['X-Content-Type-Options']='nosniff'
    return response


@login_required(login_url='/mycelium/login')
def teams_page(request):
    notice='';status=200
    if request.method=='POST':
        try:
            with transaction.atomic():
                action=request.POST.get('action')
                if action=='create':
                    if not request.user.is_active or not request.user.has_perm('plugin_manager.add_team'):
                        raise PermissionDenied('Team creation requires the add team permission.')
                    name=request.POST.get('name','').strip()
                    if not 1<=len(name)<=120: raise ValidationError('Choose a team name between 1 and 120 characters.')
                    team=Team.objects.create(name=name,created_by=request.user)
                    TeamMembership.objects.create(team=team,user=request.user,role=TeamMembership.Role.MANAGER)
                    notice='Team created. Team roles do not grant account permissions.'
                else:
                    team=get_object_or_404(Team.objects.select_for_update(),pk=request.POST.get('team_id'))
                    if not can_manage_team(request.user,team): raise PermissionDenied('Only a permitted team manager may change this team.')
                    if action=='rename':
                        name=request.POST.get('name','').strip()
                        if not 1<=len(name)<=120: raise ValidationError('Choose a team name between 1 and 120 characters.')
                        team.name=name;team.save(update_fields=['name']);notice='Team name updated.'
                    elif action in {'add','role','remove'}:
                        user=get_object_or_404(get_user_model(),username=request.POST.get('username'),is_active=True)
                        membership=TeamMembership.objects.filter(team=team,user=user).first()
                        role=request.POST.get('role',TeamMembership.Role.MEMBER)
                        if role not in TeamMembership.Role.values: raise ValidationError('Choose a valid team role.')
                        if membership and membership.role==TeamMembership.Role.MANAGER and (action=='remove' or role!=TeamMembership.Role.MANAGER) and not TeamMembership.objects.filter(team=team,role=TeamMembership.Role.MANAGER).exclude(pk=membership.pk).exists():
                            raise ValidationError('Keep at least one team manager.')
                        if action=='remove':
                            if membership: membership.delete()
                            from .certificate_authority import revoke_key
                            for key in team.signing_keys.filter(owner=user,revoked_at__isnull=True): revoke_key(key,reason='Team membership removed')
                            notice='Member removed. Their team signing keys and signed packages were revoked.'
                        else:
                            TeamMembership.objects.update_or_create(team=team,user=user,defaults={'role':role})
                            notice='Team membership updated. Account permissions were unchanged.'
                    elif action=='archive':
                        team.active=False;team.save(update_fields=['active'])
                        from .certificate_authority import revoke_key
                        for key in team.signing_keys.filter(revoked_at__isnull=True): revoke_key(key,reason='Team archived')
                        notice='Team archived and its signing keys revoked.'
                    else: raise ValidationError('Unknown team action.')
                logger.info('Team changed',extra={'event':'team_changed','operation':action,'outcome':'completed'})
        except ValidationError as error:
            notice=' '.join(error.messages);status=400
        except (ValueError,TypeError):
            notice='Choose a valid team or member.';status=400
    teams=list(visible_teams(request.user).prefetch_related('memberships__user'))
    for team in teams: team.may_manage=can_manage_team(request.user,team)
    response=render_page(request,'plugin_manager/teams.html',{'teams':teams,'can_create_team':request.user.is_active and request.user.has_perm('plugin_manager.add_team'),'notice':notice},status=status)
    response['Cache-Control']='private, no-store'
    if request.method=='POST' and request.headers.get('HX-Request')=='true': response['HX-Push-Url']='false'
    return response
