from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from .models import UserProfile

User = get_user_model()


class MeApiTests(TestCase):
    def test_authenticated_user_can_read_me(self):
        user = User.objects.create_user(username='employee', password='password123')
        client = APIClient()
        client.force_authenticate(user)

        response = client.get('/api/accounts/me/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['username'], 'employee')
        self.assertFalse(response.data['is_staff'])
        self.assertFalse(response.data['is_warehouse_operator'])

    def test_regular_employee_cannot_manage_departments_or_profiles(self):
        user = User.objects.create_user(username='employee', password='password123')
        client = APIClient()
        client.force_authenticate(user)

        department_response = client.get('/api/accounts/departments/')
        profile_response = client.get('/api/accounts/profiles/')

        self.assertEqual(department_response.status_code, 403)
        self.assertEqual(profile_response.status_code, 403)

    def test_system_administrator_can_manage_departments_and_profiles(self):
        admin = User.objects.create_superuser(
            username='system-admin',
            password='password123',
            email='',
        )
        profile_user = User.objects.create_user(username='profile-user', password='password123')
        UserProfile.objects.create(user=profile_user, employee_no='EMP-PROFILE')
        client = APIClient()
        client.force_authenticate(admin)

        department_response = client.post('/api/accounts/departments/', {
            'name': '售后部门',
            'code': 'AFTER-SALES',
        }, format='json')

        self.assertEqual(department_response.status_code, 201)
        profile_response = client.get('/api/accounts/profiles/')

        self.assertEqual(profile_response.status_code, 200)
        self.assertTrue(any(
            row['username'] == profile_user.username
            for row in profile_response.data['results']
        ))
