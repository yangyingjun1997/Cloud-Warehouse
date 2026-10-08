from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views
from .views import DepartmentViewSet, UserProfileViewSet

router = DefaultRouter()
router.register(r'departments', DepartmentViewSet, basename='department')
router.register(r'profiles', UserProfileViewSet, basename='profile')

urlpatterns = [
    path('me/', views.me, name='me'),
    *router.urls,
]
