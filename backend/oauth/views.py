import os
from django.http import JsonResponse
from django.urls import reverse
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from authlib.integrations.django_client import OAuth
from authlib.jose import jwt
from authlib.oidc.core import CodeIDToken
from django.conf import settings
import requests
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import AllowAny
from django.contrib.auth import get_user_model

oauth = OAuth()

for provider, config in settings.OAUTH_CLIENTS.items():
    oauth.register(
        name=provider.lower(),
        client_id=config['client_id'],
        client_secret=config['client_secret'],
        authorize_url=config['authorize_url'],
        access_token_url=config['token_url'],
    )

def login_oauth(request, provider):
    redirect_uri = request.GET.get('redirect_uri', '')
    next = request.GET.get('next', '/')
    # assert redirect_uri[:redirect_uri.rfind("/")] in settings.CSRF_TRUSTED_ORIGINS
    if redirect_uri == '':
        redirect_uri = request.build_absolute_uri(reverse('auth_oauth', args=[provider]))
    request.session['redirect_uri'] = redirect_uri
    request.session['next'] = next
    client = oauth.create_client(provider)
    if not client:
        return JsonResponse({"message": "unsupported provider"}, status=status.HTTP_400_BAD_REQUEST)
    return client.authorize_redirect(request, redirect_uri)

@api_view(['POST'])
@authentication_classes([SessionAuthentication])
@permission_classes([AllowAny])
def auth_oauth(request, provider):    
    code = request.data.get('code')
    state = request.data.get('state')
    redirect_uri = request.session.get('redirect_uri')
    next = request.session.get('next')
    if provider not in oauth._clients:
        return JsonResponse({"message": "unsupported provider"}, status=status.HTTP_400_BAD_REQUEST)
    
    client = oauth.create_client(provider)
    client_id = client.client_id
    client_secret = client.client_secret
    
    token = client.fetch_access_token(
        redirect_uri=redirect_uri,
        code=code,
        state=state,
        client_secret=client_secret
    )
    
    access_token = token.get('access_token')
    refresh_token = token.get('refresh_token')
    id_token = token.get('id_token')
    user_info = {}

    if provider == 'jaccount':
        claims = jwt.decode(id_token, client_secret, claims_cls=CodeIDToken)
        user_info['name'] = claims['sub']
        user_info['email'] = claims['sub'] + "@sjtu.edu.cn"

    User = get_user_model()
    user, created = User.objects.get_or_create(username=user_info.get('email')) # use email as username to make it unique
    if user:
        login(request, user)
        if created:
            user.oauth_provider = provider
            user.email = user_info.get('email')
            user.display_name = user_info.get('name')
            user.save()
        
        return JsonResponse({
                "message": "login success", 
                "next": next,
                "token": access_token,
                "refresh_token": refresh_token
            }, status=status.HTTP_200_OK)
    return JsonResponse({"message": "login failed"}, status=status.HTTP_400_BAD_REQUEST)

@api_view(['POST'])
@login_required
def logout(request):
    logout(request)
    return JsonResponse({'message': 'logout success'}, status=status.HTTP_200_OK)

@api_view(['POST'])
@authentication_classes([SessionAuthentication])
@permission_classes([AllowAny])
def oidc_refresh(request):
    refresh_token = request.data.get('refresh_token')
    if not refresh_token:
        return JsonResponse({'message': 'No refresh token available'}, status=status.HTTP_401_UNAUTHORIZED)

    provider = request.user.oauth_provider
    client = oauth.create_client(provider)
    client_id = client.client_id
    client_secret = client.client_secret

    data = {
        'grant_type': 'refresh_token',
        'refresh_token': refresh_token,
        'client_id': client_id,
        'client_secret': client_secret,
    }
    
    try:
        response = requests.post(os.getenv('JACCOUNT_TOKEN_URL'), data=data)
        response.raise_for_status()

        token_data = response.json()
        new_access_token = token_data.get('access_token')
        new_refresh_token = token_data.get('refresh_token')
        next = request.session.get('next')

        return JsonResponse({
            "message": "refresh success",
            "next": next,
            "token": new_access_token,
            "refreshToken": new_refresh_token
        }, status=status.HTTP_200_OK)
    except Exception as e:
        return JsonResponse({'message': 'Refresh failed', 'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
