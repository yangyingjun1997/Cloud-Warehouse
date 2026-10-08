from django.conf import settings
from django.conf.urls.static import static
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.contrib import admin
from django.urls import include, path
from apps.common.api_views import (
    RateLimitedObtainAuthToken,
    RevokeApiTokenView,
    RotateApiTokenView,
)

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('apps.common.urls')),
    path('api-auth/', include('rest_framework.urls')),
    path('api/auth/token/', RateLimitedObtainAuthToken.as_view(), name='api-token-auth'),
    path('api/auth/token/rotate/', RotateApiTokenView.as_view(), name='api-token-rotate'),
    path('api/auth/token/revoke/', RevokeApiTokenView.as_view(), name='api-token-revoke'),
    path('api/accounts/', include('apps.accounts.urls')),
    path('api/inventory/', include('apps.inventory.urls')),
    path('api/workflow/', include('apps.workflow.urls')),
    path('api/notifications/', include('apps.notifications.urls')),
]

if settings.DEBUG:
    urlpatterns += staticfiles_urlpatterns()
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
