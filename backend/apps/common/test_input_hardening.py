from django.test import TestCase

from apps.common.testing.fixtures import WarehouseTestFixture


class MalformedInputRegressionTests(WarehouseTestFixture, TestCase):
    def setUp(self):
        self.admin = self.make_user('input-admin', is_superuser=True)
        self.asset = self.make_asset(asset_code='SC-INPUT-0001')
        self.client.force_login(self.admin)

    def test_boolean_asset_search_limits_expression_complexity(self):
        query = '|'.join(f'part-{index}' for index in range(200))

        response = self.client.get('/assets/lookup/', {'q': query})

        self.assertEqual(response.status_code, 200)

    def test_invalid_uuid_filters_return_empty_results_instead_of_500(self):
        for path in ('/warehouse/', '/requests/', '/transactions/', '/reports/'):
            with self.subTest(path=path):
                response = self.client.get(path, {'warehouse': '<script>invalid</script>'})
                self.assertEqual(response.status_code, 200)

    def test_invalid_label_asset_id_is_rejected_without_database_error(self):
        response = self.client.post('/warehouse/qr-labels/', {
            'asset_ids': ['https://invalid.example/asset'],
            'preview': '1',
        })

        self.assertRedirects(response, '/warehouse/', fetch_redirect_response=False)

    def test_password_change_rejects_unused_put_method(self):
        response = self.client.put('/password/change/', data='{}', content_type='application/json')

        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.headers['Allow'], 'GET, POST, HEAD, OPTIONS')
