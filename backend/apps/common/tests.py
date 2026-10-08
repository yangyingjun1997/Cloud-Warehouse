from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.staticfiles import finders
import uuid
from apps.inventory.models import Asset, Category, ItemType, Location, StockItem, StockItemHolding, Warehouse
from apps.accounts.models import UserProfile
from apps.common.models import (
    ApiAccessToken,
    ApiThrottleState,
    LoginThrottleState,
    OperationAuditLog,
    SecurityAuditEvent,
    UserListPreference,
)
from apps.workflow.models import (
    ApprovalTask,
    InventoryReservation,
    InventoryTransaction,
    PurchaseRequest,
    WorkflowRequest,
    WorkflowRequestLine,
    WorkflowRequestTemplate,
)
from apps.notifications.models import Notification
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from unittest.mock import patch
from django.test import Client
from django.utils import timezone
from datetime import timedelta

User = get_user_model()


class LoginThrottleTests(TestCase):
    """登录失败限流的安全与恢复回归。"""

    @override_settings(
        LOGIN_RATE_LIMIT_FAILURES=3,
        LOGIN_RATE_LIMIT_IP_FAILURES=20,
        LOGIN_RATE_LIMIT_WINDOW_SECONDS=300,
        LOGIN_RATE_LIMIT_LOCKOUT_SECONDS=900,
    )
    def test_account_is_temporarily_blocked_after_repeated_failures(self):
        User.objects.create_user(username='throttle-user', password='correct-password')

        for _ in range(3):
            response = self.client.post('/login/', {
                'username': 'throttle-user',
                'password': 'wrong-password',
            })
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, '用户名或密码不正确，请重新输入。')

        self.assertContains(
            self.client.post('/login/', {
                'username': 'throttle-user',
                'password': 'correct-password',
            }),
            '用户名或密码不正确，请重新输入。',
        )
        state = LoginThrottleState.objects.get(key__startswith='account:')
        self.assertEqual(state.failure_count, 3)
        self.assertIsNotNone(state.blocked_until)
        self.assertNotIn('throttle-user', state.key)

    @override_settings(
        LOGIN_RATE_LIMIT_FAILURES=20,
        LOGIN_RATE_LIMIT_IP_FAILURES=2,
        LOGIN_RATE_LIMIT_WINDOW_SECONDS=300,
        LOGIN_RATE_LIMIT_LOCKOUT_SECONDS=900,
    )
    def test_ip_limit_blocks_password_attempts_across_accounts(self):
        User.objects.create_user(username='first-user', password='correct-password')
        User.objects.create_user(username='second-user', password='correct-password')

        for username in ('first-user', 'second-user'):
            response = self.client.post('/login/', {
                'username': username,
                'password': 'wrong-password',
            })
            self.assertEqual(response.status_code, 200)

        response = self.client.post('/login/', {
            'username': 'first-user',
            'password': 'correct-password',
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(LoginThrottleState.objects.filter(key__startswith='ip:').exists())
        self.assertFalse(LoginThrottleState.objects.filter(key__contains='127.0.0.1').exists())

    @override_settings(
        LOGIN_RATE_LIMIT_FAILURES=2,
        LOGIN_RATE_LIMIT_IP_FAILURES=20,
        LOGIN_RATE_LIMIT_WINDOW_SECONDS=300,
        LOGIN_RATE_LIMIT_LOCKOUT_SECONDS=900,
    )
    def test_successful_login_clears_account_failures(self):
        User.objects.create_user(username='recover-user', password='correct-password')
        for _ in range(1):
            self.client.post('/login/', {
                'username': 'recover-user',
                'password': 'wrong-password',
            })

        response = self.client.post('/login/', {
            'username': 'recover-user',
            'password': 'correct-password',
        })
        self.assertRedirects(response, '/')
        self.assertFalse(LoginThrottleState.objects.filter(key__startswith='account:').exists())

    @override_settings(
        LOGIN_RATE_LIMIT_FAILURES=1,
        LOGIN_RATE_LIMIT_IP_FAILURES=20,
        LOGIN_RATE_LIMIT_WINDOW_SECONDS=300,
        LOGIN_RATE_LIMIT_LOCKOUT_SECONDS=900,
    )
    def test_unknown_account_uses_same_message_without_account_enumeration(self):
        response = self.client.post('/login/', {
            'username': 'does-not-exist',
            'password': 'wrong-password',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '用户名或密码不正确，请重新输入。')
        self.assertNotContains(response, '账号不存在')


class LoginSecurityAuditTests(TestCase):
    """失败登录安全事件审计回归。"""

    @override_settings(
        LOGIN_RATE_LIMIT_FAILURES=3,
        LOGIN_RATE_LIMIT_IP_FAILURES=20,
        LOGIN_RATE_LIMIT_WINDOW_SECONDS=300,
        LOGIN_RATE_LIMIT_LOCKOUT_SECONDS=900,
    )
    def test_failed_login_records_only_hashed_subject_and_ip(self):
        User.objects.create_user(username='audit-user', password='correct-password')

        response = self.client.post('/login/', {
            'username': 'audit-user',
            'password': 'wrong-password',
        })

        self.assertEqual(response.status_code, 200)
        event = SecurityAuditEvent.objects.get(event_type='login_failed')
        self.assertEqual(len(event.subject_hash), 64)
        self.assertEqual(len(event.ip_hash), 64)
        self.assertNotIn('audit-user', event.subject_hash)
        self.assertNotIn('127.0.0.1', event.ip_hash)

    @override_settings(
        LOGIN_RATE_LIMIT_FAILURES=1,
        LOGIN_RATE_LIMIT_IP_FAILURES=20,
        LOGIN_RATE_LIMIT_WINDOW_SECONDS=300,
        LOGIN_RATE_LIMIT_LOCKOUT_SECONDS=900,
    )
    def test_blocked_login_records_separate_event_type(self):
        User.objects.create_user(username='blocked-audit-user', password='correct-password')
        payload = {'username': 'blocked-audit-user', 'password': 'wrong-password'}
        self.client.post('/login/', payload)

        response = self.client.post('/login/', {
            'username': 'blocked-audit-user',
            'password': 'correct-password',
        })

        self.assertEqual(response.status_code, 200)
        self.assertTrue(SecurityAuditEvent.objects.filter(event_type='login_failed').exists())
        self.assertTrue(SecurityAuditEvent.objects.filter(event_type='login_blocked').exists())

    def test_security_audit_event_is_append_only(self):
        event = SecurityAuditEvent.objects.create(
            event_type='login_failed',
            subject_hash='a' * 64,
            ip_hash='b' * 64,
        )
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            event.save()
        with self.assertRaises(ValidationError):
            event.delete()


class ApiTokenThrottleTests(TestCase):
    """认证令牌入口限流回归。"""

    @override_settings(
        API_RATE_LIMIT_IP_REQUESTS=30,
        API_RATE_LIMIT_ACCOUNT_REQUESTS=2,
        API_RATE_LIMIT_WINDOW_SECONDS=60,
    )
    def test_token_endpoint_returns_token_before_account_limit(self):
        User.objects.create_user(username='api-user', password='correct-password')

        response = self.client.post('/api/auth/token/', {
            'username': 'api-user',
            'password': 'correct-password',
        })

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json().get('token'))
        self.assertTrue(ApiThrottleState.objects.filter(key__startswith='api-account:').exists())

    @override_settings(
        API_RATE_LIMIT_IP_REQUESTS=30,
        API_RATE_LIMIT_ACCOUNT_REQUESTS=2,
        API_RATE_LIMIT_WINDOW_SECONDS=60,
    )
    def test_token_endpoint_limits_repeated_account_requests(self):
        User.objects.create_user(username='api-user', password='correct-password')

        for _ in range(2):
            response = self.client.post('/api/auth/token/', {
                'username': 'api-user',
                'password': 'wrong-password',
            })
            self.assertEqual(response.status_code, 400)

        response = self.client.post('/api/auth/token/', {
            'username': 'api-user',
            'password': 'wrong-password',
        })
        self.assertEqual(response.status_code, 429)
        self.assertGreaterEqual(int(response['Retry-After']), 1)
        self.assertLessEqual(int(response['Retry-After']), 60)
        self.assertEqual(response.json()['detail'], '请求过于频繁，请稍后再试。')

    @override_settings(
        API_RATE_LIMIT_IP_REQUESTS=2,
        API_RATE_LIMIT_ACCOUNT_REQUESTS=30,
        API_RATE_LIMIT_WINDOW_SECONDS=60,
    )
    def test_token_endpoint_limits_ip_even_when_usernames_change(self):
        for username in ('api-first-user', 'api-second-user'):
            User.objects.create_user(username=username, password='correct-password')
            response = self.client.post('/api/auth/token/', {
                'username': username,
                'password': 'wrong-password',
            })
            self.assertEqual(response.status_code, 400)

        response = self.client.post('/api/auth/token/', {
            'username': 'api-third-user',
            'password': 'wrong-password',
        })
        self.assertEqual(response.status_code, 429)
        state = ApiThrottleState.objects.get(key__startswith='api-ip:')
        self.assertNotIn('127.0.0.1', state.key)


class ApiTokenLifecycleTests(TestCase):
    """API 令牌过期、轮换和撤销回归。"""

    def setUp(self):
        self.user = User.objects.create_user(username='token-user', password='correct-password')
        self.client = APIClient()

    def _issue(self):
        response = self.client.post('/api/auth/token/', {
            'username': 'token-user',
            'password': 'correct-password',
        })
        self.assertEqual(response.status_code, 200)
        return response.json()['token']

    def test_issued_token_authenticates_api_and_raw_value_is_not_stored(self):
        raw_token = self._issue()

        self.client.credentials(HTTP_AUTHORIZATION=f'Token {raw_token}')
        response = self.client.get('/api/accounts/me/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['username'], 'token-user')
        token = ApiAccessToken.objects.get(user=self.user)
        self.assertNotEqual(token.token_hash, raw_token)
        self.assertNotIn(raw_token, token.token_hash)

    @override_settings(API_TOKEN_TTL_SECONDS=60)
    def test_expired_token_is_rejected(self):
        from django.utils import timezone
        from datetime import timedelta

        raw_token = self._issue()
        token = ApiAccessToken.objects.get(user=self.user)
        ApiAccessToken.objects.filter(pk=token.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {raw_token}')

        response = self.client.get('/api/accounts/me/')

        self.assertEqual(response.status_code, 401)
        self.assertContains(response, '认证令牌无效或已过期', status_code=401)

    def test_rotation_invalidates_old_token_and_returns_new_token(self):
        old_token = self._issue()
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {old_token}')

        response = self.client.post('/api/auth/token/rotate/')

        self.assertEqual(response.status_code, 200)
        new_token = response.json()['token']
        self.assertNotEqual(old_token, new_token)
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {new_token}')
        self.assertEqual(self.client.get('/api/accounts/me/').status_code, 200)

        self.client.credentials(HTTP_AUTHORIZATION=f'Token {old_token}')
        old_response = self.client.get('/api/accounts/me/')
        self.assertEqual(old_response.status_code, 401)

        self.client.credentials(HTTP_AUTHORIZATION=f'Token {new_token}')
        self.assertEqual(self.client.get('/api/accounts/me/').status_code, 200)

    def test_revoke_invalidates_current_token(self):
        raw_token = self._issue()
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {raw_token}')

        response = self.client.post('/api/auth/token/revoke/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['detail'], '当前令牌已撤销。')
        revoked_response = self.client.get('/api/accounts/me/')
        self.assertEqual(revoked_response.status_code, 401)


class SecurityStateCleanupTests(TestCase):
    """安全状态清理命令回归。"""

    @override_settings(SECURITY_STATE_RETENTION_SECONDS=86400)
    def test_dry_run_reports_without_deleting_and_real_run_keeps_active_data(self):
        from datetime import timedelta
        from io import StringIO
        from django.core.management import call_command

        now = timezone.now()
        old = now - timedelta(days=3)
        LoginThrottleState.objects.create(key='old-login', failure_count=2)
        LoginThrottleState.objects.filter(key='old-login').update(updated_at=old)
        ApiThrottleState.objects.create(key='old-api', request_count=2, window_started_at=old)
        ApiThrottleState.objects.filter(key='old-api').update(updated_at=old)
        SecurityAuditEvent.objects.create(
            event_type='login_failed',
            subject_hash='c' * 64,
            ip_hash='d' * 64,
        )
        SecurityAuditEvent.objects.filter(subject_hash='c' * 64).update(created_at=old, updated_at=old)
        ApiAccessToken.objects.create(
            user=User.objects.create_user(username='cleanup-user', password='password123'),
            token_hash='a' * 64,
            token_prefix='cleanup-old',
            expires_at=old,
        )
        fresh_user = User.objects.create_user(username='fresh-token-user', password='password123')
        active = ApiAccessToken.objects.create(
            user=fresh_user,
            token_hash='b' * 64,
            token_prefix='cleanup-live',
            expires_at=now + timedelta(days=10),
        )

        preview = StringIO()
        call_command('prune_security_states', '--dry-run', stdout=preview)
        self.assertIn('登录限流 1 条', preview.getvalue())
        self.assertTrue(LoginThrottleState.objects.filter(key='old-login').exists())

        output = StringIO()
        call_command('prune_security_states', stdout=output)

        self.assertFalse(LoginThrottleState.objects.filter(key='old-login').exists())
        self.assertFalse(ApiThrottleState.objects.filter(key='old-api').exists())
        self.assertFalse(SecurityAuditEvent.objects.filter(subject_hash='c' * 64).exists())
        self.assertFalse(ApiAccessToken.objects.filter(token_prefix='cleanup-old').exists())
        self.assertTrue(ApiAccessToken.objects.filter(pk=active.pk).exists())
        self.assertIn('安全状态清理完成', output.getvalue())


class DashboardAndAdminTests(TestCase):
    def setUp(self):
        category = Category.objects.create(name='测试分类', code='TEST')
        item_type = ItemType.objects.create(name='测试类型', code='TEST-TYPE', category=category)
        warehouse = Warehouse.objects.create(name='售后中心仓', code='AFTER-WH')
        location = Location.objects.create(warehouse=warehouse, name='A 区货架', code='A-01')
        self.asset = Asset.objects.create(
            name='测试机器人本体',
            item_type=item_type,
            warehouse=warehouse,
            location=location,
            asset_code='WEB-ZC-001',
            serial_number='SN-WEB-001',
            qr_value='QR-WEB-001',
        )
        self.employee = User.objects.create_user(username='employee', password='password123')
        self.operator = User.objects.create_user(username='operator', password='password123', is_staff=True)
        self.operator.groups.add(Group.objects.create(name='warehouse_staff'))

    def test_dashboard_requires_web_login(self):
        response = self.client.get('/')
        self.assertRedirects(response, '/login/?next=/')

    def test_login_and_application_brand_marks_use_warehouse_character(self):
        response = self.client.get('/login/')

        self.assertContains(response, '<span class="mark">仓</span>', html=True)
        self.assertNotContains(response, '海塔')

    def test_regular_employee_can_use_web_login(self):
        response = self.client.post('/login/', {
            'username': 'employee',
            'password': 'password123',
        })

        self.assertRedirects(response, '/')

    def test_logout_uses_post_and_clears_the_authenticated_session(self):
        self.client.force_login(self.employee)

        response = self.client.post('/logout/')

        self.assertRedirects(response, '/login/')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_employee_dashboard_hides_inventory_counts_and_admin_navigation(self):
        self.client.force_login(self.employee)
        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '我的工作台')
        self.assertContains(response, '资产检索')
        self.assertNotContains(response, '在库可用资产')
        self.assertNotContains(response, '系统管理')
        self.assertNotContains(response, '/health/')
        self.assertNotContains(response, '/api/inventory/assets/')

    def test_operator_dashboard_has_warehouse_information(self):
        self.client.force_login(self.operator)
        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '仓库工作台')
        self.assertContains(response, '在库可用资产')
        self.assertContains(response, '审批任务')
        self.assertContains(response, '/warehouse/?tab=assets&amp;status=in_stock')
        self.assertNotContains(response, '系统管理')

    def test_operator_asset_lookup_searches_person_requests_and_current_assets(self):
        self.employee.first_name = '测试员工'
        self.employee.save(update_fields=['first_name'])
        self.asset.current_holder = self.employee
        self.asset.status = Asset.Status.BORROWED
        self.asset.save()
        workflow_request = WorkflowRequest.objects.create(
            request_type=WorkflowRequest.RequestType.BORROW,
            applicant=self.employee,
            recipient_name='测试员工',
            status=WorkflowRequest.Status.DONE,
        )
        WorkflowRequestLine.objects.create(request=workflow_request, asset=self.asset)

        self.client.force_login(self.operator)
        response = self.client.get('/assets/lookup/', {'q': 'employee'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '人员与持有设备')
        self.assertContains(response, '测试员工')
        self.assertContains(response, workflow_request.request_no)
        self.assertContains(response, self.asset.asset_code)

    def test_operator_asset_lookup_searches_external_recipient_name(self):
        workflow_request = WorkflowRequest.objects.create(
            request_type=WorkflowRequest.RequestType.REPAIR,
            applicant=self.employee,
            recipient_name='外部返修人',
            status=WorkflowRequest.Status.DONE,
        )
        self.client.force_login(self.operator)
        response = self.client.get('/assets/lookup/', {'q': '返修人'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '外部返修人')
        self.assertContains(response, workflow_request.request_no)

    def test_web_asset_lookup_accepts_asset_code_serial_and_qr_value(self):
        self.client.force_login(self.employee)

        # 资产编码 / SN 是唯一标识，直接重定向到履历页；SN 可能不唯一，仍走列表。
        for identifier in [self.asset.asset_code, self.asset.qr_value]:
            response = self.client.get('/assets/lookup/', {'q': identifier})
            self.assertRedirects(
                response,
                f'/warehouse/assets/{self.asset.id}/lifecycle/',
                fetch_redirect_response=False,
            )
        response = self.client.get('/assets/lookup/', {'q': self.asset.serial_number})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '测试机器人本体')
        self.assertContains(response, '可申请')
        self.assertNotContains(response, '售后中心仓')

        self.assertContains(response, 'warehouse/scanner.js')
        self.assertContains(response, 'warehouse/vendor/zxing-browser-0.1.5.min.js')

    def test_scanner_static_dependencies_are_discoverable(self):
        self.assertIsNotNone(finders.find('warehouse/scanner.js'))
        self.assertIsNotNone(finders.find('warehouse/vendor/zxing-browser-0.1.5.min.js'))

    def test_employee_can_submit_a_mobile_web_request_without_admin_access(self):
        self.client.force_login(self.employee)
        response = self.client.post('/requests/new/', {
            'request_type': WorkflowRequest.RequestType.BORROW,
            'asset_id': str(self.asset.id),
            'quantity': '1',
            'recipient_name': '测试员工',
            'recipient_phone': '13800138000',
            'usage_location': '研发实验室',
            'expected_return_date': '2026-09-01',
            'reason': '现场测试',
        })
        self.assertRedirects(response, '/requests/')
        workflow_request = WorkflowRequest.objects.get(applicant=self.employee)
        self.assertEqual(workflow_request.status, WorkflowRequest.Status.PENDING)
        self.assertEqual(workflow_request.lines.get().asset_id, self.asset.id)
        self.assertEqual(workflow_request.usage_location, '研发实验室')
        self.assertEqual(workflow_request.application_date, timezone.localdate())

    def test_employee_can_submit_multiple_assets_and_stock_items_in_one_request(self):
        second_asset = Asset.objects.create(
            name='测试机器人载荷',
            item_type=self.asset.item_type,
            warehouse=self.asset.warehouse,
            location=self.asset.location,
        )
        stock_item = StockItem.objects.create(
            name='测试紧固件',
            code='FASTENER-WEB-001',
            item_type=self.asset.item_type,
            warehouse=self.asset.warehouse,
            location=self.asset.location,
            quantity=20,
            unit='个',
        )
        self.client.force_login(self.employee)

        response = self.client.post('/requests/new/', {
            'request_type': WorkflowRequest.RequestType.BORROW,
            'asset_ids': [str(self.asset.id), str(second_asset.id)],
            'stock_item_ids': [str(stock_item.id)],
            f'stock_quantity_{stock_item.id}': '3',
            'recipient_name': '测试员工',
            'recipient_phone': '13800138000',
            'usage_location': '研发实验室',
            'expected_return_date': '2026-09-01',
            'reason': '批量测试',
        })

        self.assertRedirects(response, '/requests/')
        workflow_request = WorkflowRequest.objects.get(reason='批量测试')
        self.assertEqual(workflow_request.lines.count(), 3)
        self.assertEqual(
            workflow_request.lines.get(stock_item=stock_item).quantity,
            3,
        )
        self.assertTrue(all(line.quantity == 1 for line in workflow_request.lines.filter(asset__isnull=False)))

    def test_request_form_uses_fixed_asset_quantity_and_per_stock_item_quantity(self):
        stock_item = StockItem.objects.create(
            name='测试线缆',
            code='CABLE-WEB-001',
            item_type=self.asset.item_type,
            warehouse=self.asset.warehouse,
            location=self.asset.location,
            quantity=6,
            unit='根',
        )
        self.client.force_login(self.employee)

        response = self.client.get('/requests/new/', {
            'q': '测试',
            'asset_ids': [str(self.asset.id)],
            'stock_item_ids': [str(stock_item.id)],
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '单件资产固定 1 件')
        self.assertContains(response, f'name="stock_quantity_{stock_item.id}"')
        self.assertNotContains(response, 'name="quantity"')
        self.assertEqual(response.content.count(b'id="item-query"'), 1)
        self.assertContains(response, '>扫码<')
        self.assertContains(response, '开启手电筒')
        self.assertContains(response, '切换镜头')
        self.assertContains(response, '本次扫码结果')
        self.assertNotContains(response, '直接加入')
        self.assertNotContains(response, '连续扫码')
        self.assertContains(response, 'scan-result-list')
        self.assertContains(response, 'selected-eligibility')
        self.assertContains(response, 'response.status === 409 && result.item')

    def test_candidate_scan_returns_persistent_asset_identifier(self):
        self.client.force_login(self.employee)

        response = self.client.get('/requests/candidates/scan/', {
            'value': self.asset.qr_value,
            'request_type': WorkflowRequest.RequestType.BORROW,
        })

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])
        self.assertEqual(response.json()['item']['id'], str(self.asset.id))

    def test_candidate_scan_returns_ineligible_asset_for_form_correction(self):
        self.client.force_login(self.employee)

        response = self.client.get('/requests/candidates/scan/', {
            'value': self.asset.qr_value,
            'request_type': WorkflowRequest.RequestType.RETURN,
        })

        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.json()['eligible'])
        self.assertEqual(response.json()['item']['id'], str(self.asset.id))
        self.assertEqual(response.json()['item']['status_label'], '在库')
        self.assertIn('不能办理“归还”', response.json()['message'])

    def test_ineligible_asset_can_be_saved_in_draft_but_not_submitted(self):
        self.client.force_login(self.employee)
        draft_response = self.client.post('/requests/new/', {
            'action': 'save_draft',
            'request_type': WorkflowRequest.RequestType.RETURN,
            'asset_ids': [str(self.asset.id)],
            'application_date': timezone.localdate().isoformat(),
        })

        draft = WorkflowRequest.objects.get(applicant=self.employee)
        self.assertRedirects(draft_response, f'/requests/{draft.id}/edit/')
        self.assertEqual(draft.status, WorkflowRequest.Status.DRAFT)
        self.assertEqual(draft.lines.get().asset_id, self.asset.id)
        self.assertFalse(InventoryReservation.objects.filter(request_line__request=draft).exists())

        submit_response = self.client.post(f'/requests/{draft.id}/edit/', {
            'action': 'submit',
            'request_type': WorkflowRequest.RequestType.RETURN,
            'asset_ids': [str(self.asset.id)],
            'application_date': timezone.localdate().isoformat(),
            'reason': '错误归还状态测试',
        })

        self.assertEqual(submit_response.status_code, 200)
        self.assertContains(submit_response, '当前状态为“在库”，不能办理“归还”')
        draft.refresh_from_db()
        self.assertEqual(draft.status, WorkflowRequest.Status.DRAFT)

    def test_quick_process_rejects_ineligible_asset_state(self):
        self.client.force_login(self.operator)

        response = self.client.post('/warehouse/quick-process/', {
            'request_type': WorkflowRequest.RequestType.RETURN,
            'asset_ids': [str(self.asset.id)],
            'target_warehouse': str(self.asset.warehouse_id),
            'target_location': str(self.asset.location_id),
            'transaction_date': timezone.localdate().isoformat(),
            'contact_source': '现场',
            'reason': '在库资产不能归还',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '当前状态为“在库”，不能办理“归还”')
        self.assertFalse(WorkflowRequest.objects.filter(is_quick_process=True).exists())

    def test_quick_process_uses_unified_search_and_scanner_input(self):
        self.client.force_login(self.operator)

        response = self.client.get('/warehouse/quick-process/', {'q': '测试'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.count(b'id="item-query"'), 1)
        self.assertContains(response, 'warehouse/scanner.js')
        self.assertContains(response, '开启手电筒')
        self.assertContains(response, '切换镜头')
        self.assertContains(response, '本次扫码结果')
        self.assertNotContains(response, '直接加入')
        self.assertNotContains(response, '连续扫码')

    def test_reservation_dashboard_explains_inventory_holding_stages(self):
        self.client.force_login(self.operator)

        response = self.client.get('/reservations/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '库存占用与借用')
        self.assertContains(response, '待审批占用')
        self.assertContains(response, '待出库占用')
        self.assertNotContains(response, '预约与借用')

    def test_read_notifications_and_closed_requests_use_compact_rows(self):
        Notification.objects.create(
            recipient=self.employee, title='已读审批结果', content='申请已经通过。', is_read=True,
        )
        Notification.objects.create(
            recipient=self.employee, title='未读待处理事项', content='请及时处理。', is_read=False,
        )
        WorkflowRequest.objects.create(
            applicant=self.employee,
            request_type=WorkflowRequest.RequestType.BORROW,
            status=WorkflowRequest.Status.DONE,
            reason='已完成申请',
        )
        WorkflowRequest.objects.create(
            applicant=self.employee,
            request_type=WorkflowRequest.RequestType.BORROW,
            status=WorkflowRequest.Status.PENDING,
            reason='待审批申请',
        )
        self.client.force_login(self.employee)

        notification_response = self.client.get('/notifications/')
        request_response = self.client.get('/requests/')

        self.assertContains(notification_response, 'notification-item is-read')
        self.assertContains(notification_response, 'notification-item is-unread')
        self.assertContains(request_response, 'request-row is-closed')

    def test_request_form_requires_traceability_fields(self):
        self.client.force_login(self.employee)
        response = self.client.post('/requests/new/', {
            'request_type': WorkflowRequest.RequestType.ISSUE,
            'asset_id': str(self.asset.id),
            'quantity': '1',
            'reason': '缺少交接信息的测试',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '请填写实际使用人或接收人。')
        self.assertFalse(WorkflowRequest.objects.filter(reason='缺少交接信息的测试').exists())

    def test_employee_can_save_and_continue_request_draft(self):
        self.client.force_login(self.employee)
        response = self.client.post('/requests/new/', {
            'action': 'save_draft',
            'request_type': WorkflowRequest.RequestType.BORROW,
            'application_date': timezone.localdate().isoformat(),
        })
        draft = WorkflowRequest.objects.get(applicant=self.employee)
        self.assertRedirects(response, f'/requests/{draft.id}/edit/')
        self.assertEqual(draft.status, WorkflowRequest.Status.DRAFT)
        self.assertFalse(InventoryReservation.objects.filter(request_line__request=draft).exists())

        response = self.client.post(f'/requests/{draft.id}/edit/', {
            'action': 'submit',
            'request_type': WorkflowRequest.RequestType.BORROW,
            'asset_ids': [str(self.asset.id)],
            'recipient_name': '测试员工',
            'recipient_phone': '13800138000',
            'usage_location': '研发实验室',
            'application_date': timezone.localdate().isoformat(),
            'expected_return_date': '2026-09-01',
            'reason': '草稿提交测试',
        })
        self.assertRedirects(response, '/requests/')
        draft.refresh_from_db()
        self.assertEqual(draft.status, WorkflowRequest.Status.PENDING)
        self.assertTrue(InventoryReservation.objects.filter(request_line__request=draft).exists())

    def test_request_template_saves_form_values_without_inventory_items(self):
        self.client.force_login(self.employee)
        response = self.client.post('/requests/new/', {
            'action': 'save_template',
            'template_name': '研发借用',
            'request_type': WorkflowRequest.RequestType.BORROW,
            'recipient_name': '测试员工',
            'recipient_phone': '13800138000',
            'usage_location': '研发实验室',
            'application_date': timezone.localdate().isoformat(),
            'expected_return_date': (timezone.localdate() + timedelta(days=5)).isoformat(),
            'reason': '研发测试',
        })
        template = WorkflowRequestTemplate.objects.get(owner=self.employee)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(template.default_borrow_days, 5)
        self.assertFalse(WorkflowRequest.objects.filter(applicant=self.employee).exists())

    def test_exact_scan_endpoint_adds_item_and_reports_reservation(self):
        self.client.force_login(self.employee)
        response = self.client.get('/requests/candidates/scan/', {
            'value': self.asset.qr_value,
            'request_type': WorkflowRequest.RequestType.BORROW,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['item']['id'], str(self.asset.id))

        workflow_request = WorkflowRequest.objects.create(
            applicant=self.employee,
            request_type=WorkflowRequest.RequestType.BORROW,
            status=WorkflowRequest.Status.PENDING,
            usage_location='测试区',
        )
        line = workflow_request.lines.create(asset=self.asset, quantity=1)
        InventoryReservation.objects.create(request_line=line, asset=self.asset, quantity=1)
        response = self.client.get('/requests/candidates/scan/', {
            'value': self.asset.asset_code,
            'request_type': WorkflowRequest.RequestType.BORROW,
        })
        self.assertEqual(response.status_code, 409)
        self.assertIn('当前状态为', response.json()['message'])
        self.assertIn('不能办理', response.json()['message'])

    def test_operator_can_quick_process_multiple_asset_and_stock_lines(self):
        stock_item = StockItem.objects.create(
            name='测试替换件',
            code='QUICK-PART-001',
            item_type=self.asset.item_type,
            warehouse=self.asset.warehouse,
            location=self.asset.location,
            quantity=8,
            unit='个',
        )
        self.client.force_login(self.operator)

        response = self.client.post('/warehouse/quick-process/', {
            'request_type': WorkflowRequest.RequestType.ISSUE,
            'asset_ids': [str(self.asset.id)],
            'stock_item_ids': [str(stock_item.id)],
            f'stock_quantity_{stock_item.id}': '2',
            'recipient_name': '现场工程师',
            'recipient_phone': '13800138000',
            'usage_location': '客户现场',
            'contact_source': '电话',
            'reason': '紧急替换',
        })

        workflow_request = WorkflowRequest.objects.get(reason='紧急替换')
        self.assertRedirects(response, f'/workflow/{workflow_request.id}/')
        self.assertTrue(workflow_request.is_quick_process)
        self.assertEqual(workflow_request.status, WorkflowRequest.Status.DONE)
        self.assertEqual(workflow_request.lines.count(), 2)
        stock_item.refresh_from_db()
        self.assertEqual(stock_item.quantity, 6)

    def test_report_dashboard_and_export_are_operator_only(self):
        self.client.logout()
        response = self.client.get('/reports/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

        self.client.force_login(self.employee)
        response = self.client.get('/reports/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/')
        response = self.client.get('/reports/', follow=True)
        self.assertContains(response, '当前账号没有报表查看权限。')

        self.client.force_login(self.operator)
        response = self.client.get('/reports/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '报表中心')
        response = self.client.get('/reports/transactions.csv')
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/csv', response['Content-Type'])

    def test_report_uses_four_state_dimensions_and_keeps_in_warehouse_cost(self):
        self.asset.purchase_amount = '50.00'
        self.asset.save(update_fields=['purchase_amount'])
        maintenance_asset = Asset.objects.create(
            name='维修中的测试资产',
            item_type=self.asset.item_type,
            warehouse=self.asset.warehouse,
            location=self.asset.location,
            asset_code='WEB-ZC-003',
            serial_number='SN-WEB-003',
            status=Asset.Status.MAINTENANCE,
            purchase_amount='80.00',
        )
        self.client.force_login(self.operator)

        response = self.client.get('/reports/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '实物位置')
        self.assertContains(response, '可用性')
        self.assertContains(response, '质量状态')
        self.assertContains(response, '处置状态')
        self.assertContains(response, '待维修')
        self.assertContains(response, '130.00')
        self.assertNotEqual(maintenance_asset.availability_state, Asset.AvailabilityState.AVAILABLE)

    def test_transaction_list_filters_by_business_date_and_shows_request_link(self):
        workflow_request = WorkflowRequest.objects.create(
            applicant=self.employee,
            request_type=WorkflowRequest.RequestType.ISSUE,
            inventory_no='IO-20260827-000001',
            application_date=timezone.localdate(),
            transaction_date=timezone.localdate(),
        )
        transaction = InventoryTransaction.objects.create(
            request=workflow_request,
            asset=self.asset,
            actor=self.operator,
            action=WorkflowRequest.RequestType.ISSUE,
            business_date=timezone.localdate(),
            quantity=1,
        )

        self.client.force_login(self.operator)
        response = self.client.get('/transactions/', {
            'transaction_start': timezone.localdate().isoformat(),
            'transaction_end': timezone.localdate().isoformat(),
            'q': self.asset.asset_code,
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '出入库记录')
        self.assertContains(response, workflow_request.inventory_no)
        self.assertContains(response, workflow_request.request_no)
        self.assertContains(response, str(transaction.business_date))

    def test_warehouse_management_is_available_to_warehouse_users(self):
        self.client.force_login(self.operator)
        response = self.client.get('/warehouse/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '仓库管理')
        self.assertContains(response, '新增物品')
        self.assertContains(response, self.asset.asset_code)

        response = self.client.get('/warehouse/assets/new/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '资产状态、持有人和归属部门由审批后的出入库流程维护')

    def test_warehouse_management_filters_assets_by_four_state_dimensions(self):
        borrowed_asset = Asset.objects.create(
            name='已借出测试资产',
            item_type=self.asset.item_type,
            warehouse=self.asset.warehouse,
            location=self.asset.location,
            asset_code='WEB-ZC-002',
            serial_number='SN-WEB-002',
            status=Asset.Status.BORROWED,
        )
        self.client.force_login(self.operator)

        response = self.client.get('/warehouse/', {'availability_state': Asset.AvailabilityState.AVAILABLE})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.asset.asset_code)
        self.assertNotContains(response, borrowed_asset.asset_code)

        response = self.client.get('/warehouse/', {'location_state': Asset.LocationState.OUT_ON_LOAN})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, borrowed_asset.asset_code)
        self.assertNotContains(response, self.asset.asset_code)

    def test_asset_lookup_uses_availability_dimension_for_request_label(self):
        Asset.objects.filter(pk=self.asset.pk).update(
            availability_state=Asset.AvailabilityState.RESERVED,
        )
        self.client.force_login(self.employee)

        response = self.client.get('/assets/lookup/', {'q': self.asset.serial_number})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '暂不可申请')
        self.assertNotContains(response, '>可申请<')

    def test_replenishment_suggestions_show_shortage_and_csv_export(self):
        stock_item = StockItem.objects.create(
            name='低库存测试耗材', code='LOW-REPLENISH-001', quantity=2,
            safety_stock=5, unit='个', unit_cost='12.50', warehouse=self.asset.warehouse,
        )
        self.client.force_login(self.operator)
        response = self.client.get('/warehouse/replenishment/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, stock_item.name)
        self.assertContains(response, '补 3 个')
        self.assertContains(response, '37.50')

        response = self.client.get('/warehouse/replenishment/?q=LOW-REPLENISH-001&download=csv')
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/csv', response['Content-Type'])
        self.assertIn(stock_item.name, response.content.decode('utf-8-sig'))

    def test_replenishment_suggestions_require_inventory_permission(self):
        self.client.force_login(self.employee)
        response = self.client.get('/warehouse/replenishment/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/')
        response = self.client.get('/warehouse/replenishment/', follow=True)
        self.assertContains(response, '当前账号没有补货建议查看权限。')

    def test_replenishment_can_create_edit_and_delete_purchase_draft(self):
        stock_item = StockItem.objects.create(
            name='采购草稿测试配件', code='PURCHASE-DRAFT-001', quantity=1,
            safety_stock=4, unit='个', unit_cost='8.50', warehouse=self.asset.warehouse,
            location=self.asset.location,
        )
        self.client.force_login(self.operator)
        response = self.client.post('/warehouse/replenishment/', {
            'action': 'create_purchase_draft',
            'selected_item': [f'stock_item:{stock_item.id}'],
            f'purchase_quantity_stock_item_{stock_item.id}': '3',
            'reason': '补足维修常用配件',
            'note': '测试草稿',
        })
        draft = PurchaseRequest.objects.get()
        self.assertRedirects(response, f'/warehouse/purchase-drafts/{draft.id}/edit/')
        line = draft.lines.get()
        self.assertEqual(line.quantity, 3)
        self.assertEqual(line.reference_amount, 25.50)
        self.assertEqual(line.warehouse_snapshot, self.asset.warehouse.name)
        self.assertTrue(OperationAuditLog.objects.filter(action='purchase_draft_created', target_id=draft.id).exists())

        response = self.client.get('/warehouse/purchase-drafts/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, draft.purchase_no)
        response = self.client.get(f'/warehouse/purchase-drafts/{draft.id}/edit/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '核对采购草稿')

        response = self.client.post(f'/warehouse/purchase-drafts/{draft.id}/edit/', {
            f'quantity_{line.id}': '5',
            'reason': '修改采购数量',
            'note': '已确认下月任务',
        })
        self.assertRedirects(response, '/warehouse/purchase-drafts/')
        line.refresh_from_db()
        draft.refresh_from_db()
        self.assertEqual(line.quantity, 5)
        self.assertEqual(line.reference_amount, 42.50)
        self.assertEqual(draft.reason, '修改采购数量')
        self.assertTrue(OperationAuditLog.objects.filter(action='purchase_draft_updated', target_id=draft.id).exists())

        response = self.client.post(f'/warehouse/purchase-drafts/{draft.id}/delete/')
        self.assertRedirects(response, '/warehouse/purchase-drafts/')
        self.assertFalse(PurchaseRequest.objects.filter(pk=draft.id).exists())
        self.assertTrue(OperationAuditLog.objects.filter(action='purchase_draft_deleted', target_id=draft.id).exists())

    def test_purchase_draft_can_be_submitted_and_approved_without_changing_inventory(self):
        stock_item = StockItem.objects.create(
            name='采购审批测试配件', code='PURCHASE-APPROVAL-001', quantity=2,
            safety_stock=5, unit='个', warehouse=self.asset.warehouse,
        )
        approver = User.objects.create_user(username='purchase-approver', password='password123')
        approver.groups.add(Group.objects.get_or_create(name='warehouse_approval')[0])
        self.client.force_login(self.operator)
        self.client.post('/warehouse/replenishment/', {
            'action': 'create_purchase_draft',
            'selected_item': [f'stock_item:{stock_item.id}'],
            f'purchase_quantity_stock_item_{stock_item.id}': '3',
            'reason': '采购审批测试',
        })
        draft = PurchaseRequest.objects.get()
        response = self.client.post('/warehouse/purchase-drafts/', {
            'action': 'submit_purchase',
            'purchase_id': str(draft.id),
        })
        self.assertRedirects(response, '/warehouse/purchase-drafts/')
        draft.refresh_from_db()
        self.assertEqual(draft.status, PurchaseRequest.Status.PENDING_APPROVAL)
        self.assertEqual(stock_item.quantity, 2)
        self.assertTrue(Notification.objects.filter(
            recipient=approver,
            related_model='PurchaseRequest',
            related_object_id=str(draft.id),
        ).exists())

        self.client.force_login(self.employee)
        response = self.client.post(f'/warehouse/purchase-drafts/{draft.id}/', {
            'action': 'approve',
        }, follow=True)
        self.assertContains(response, '当前账号没有采购申请查看权限。')
        draft.refresh_from_db()
        self.assertEqual(draft.status, PurchaseRequest.Status.PENDING_APPROVAL)

        self.client.force_login(approver)
        response = self.client.get('/approvals/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, draft.purchase_no)
        response = self.client.post(f'/warehouse/purchase-drafts/{draft.id}/', {
            'action': 'approve',
            'comment': '库存缺口明确，同意采购。',
        })
        self.assertRedirects(response, f'/warehouse/purchase-drafts/{draft.id}/')
        draft.refresh_from_db()
        self.assertEqual(draft.status, PurchaseRequest.Status.APPROVED)
        self.assertEqual(draft.approved_by_id, approver.id)
        self.assertTrue(Notification.objects.filter(
            recipient=self.operator,
            related_model='PurchaseRequest',
            related_object_id=str(draft.id),
            title='采购申请已通过',
        ).exists())
        purchase_notification = Notification.objects.get(
            recipient=self.operator,
            related_model='PurchaseRequest',
            related_object_id=str(draft.id),
            title='采购申请已通过',
        )
        self.client.force_login(self.operator)
        response = self.client.get(f'/notifications/{purchase_notification.id}/open/')
        self.assertRedirects(response, f'/warehouse/purchase-drafts/{draft.id}/')
        self.assertFalse(InventoryTransaction.objects.exists())
        self.assertTrue(OperationAuditLog.objects.filter(action='purchase_request_approved', target_id=draft.id).exists())

    def test_asset_edit_page_shows_the_asset_qr_code(self):
        self.client.force_login(self.operator)
        response = self.client.get(f'/warehouse/assets/{self.asset.id}/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '本系统资产二维码')
        self.assertContains(response, self.asset.qr_value)
        self.assertContains(response, 'data:image/png;base64,')

    def test_asset_edit_cannot_bypass_workflow_controlled_state_and_writes_audit_log(self):
        self.client.force_login(self.operator)
        response = self.client.post(f'/warehouse/assets/{self.asset.id}/', {
            'name': self.asset.name,
            'item_type': str(self.asset.item_type_id),
            'warehouse': str(self.asset.warehouse_id),
            'location': str(self.asset.location_id),
            'status': 'borrowed',
            'current_holder': str(self.employee.id),
        })

        self.assertRedirects(response, '/warehouse/')
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, Asset.Status.IN_STOCK)
        self.assertIsNone(self.asset.current_holder_id)
        audit_log = OperationAuditLog.objects.get(target_id=self.asset.id)
        self.assertEqual(audit_log.action, 'asset_updated')

    def test_stock_item_edit_does_not_expose_direct_quantity_change(self):
        stock_item = StockItem.objects.create(name='测试备件', code='PART-001', quantity=8)
        self.client.force_login(self.operator)
        response = self.client.get(f'/warehouse/stock-items/{stock_item.id}/')

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '当前库存数量')
        self.assertNotContains(response, '初始库存数量')

    def test_operator_can_see_location_information_while_reviewing_request(self):
        workflow_request = WorkflowRequest.objects.create(
            applicant=self.employee,
            request_type=WorkflowRequest.RequestType.BORROW,
            status=WorkflowRequest.Status.WAITING_WAREHOUSE,
            reason='定位测试',
        )
        WorkflowRequestLine.objects.create(request=workflow_request, asset=self.asset)
        ApprovalTask.objects.create(request=workflow_request, status=ApprovalTask.Status.APPROVED)
        self.client.force_login(self.operator)

        response = self.client.get(f'/workflow/{workflow_request.id}/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '当前仓库 / 库位')
        self.assertContains(response, '售后中心仓 / A-01')
        self.assertContains(response, '申请日期')
        self.assertContains(response, '关联申请单')
        self.assertContains(response, workflow_request.request_no)

    def test_warehouse_administrator_can_manage_base_data(self):
        administrator = User.objects.create_user(
            username='warehouse-admin', password='password123', is_staff=True,
        )
        administrator.groups.add(Group.objects.create(name='warehouse_admin'))
        self.client.force_login(administrator)

        response = self.client.get('/warehouse/setup/warehouses/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '售后中心仓')

        response = self.client.post('/warehouse/setup/categories/new/', {
            'name': '网络设备',
            'code': 'NETWORK',
        })
        self.assertRedirects(response, '/warehouse/setup/categories/')
        self.assertTrue(Category.objects.filter(code='NETWORK').exists())

    def test_warehouse_staff_cannot_manage_base_data(self):
        self.client.force_login(self.operator)
        response = self.client.get('/warehouse/setup/warehouses/')
        self.assertEqual(response.status_code, 302)

    def test_warehouse_operator_can_print_selected_asset_qr_labels(self):
        self.client.force_login(self.operator)
        response = self.client.post('/warehouse/qr-labels/', {
            'asset_ids': [str(self.asset.id)],
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.asset.asset_code)
        self.assertContains(response, '打印标签')

    def test_superuser_can_open_warehouse_management(self):
        administrator = User.objects.create_superuser(
            username='warehouse-superuser', password='password123', email='',
        )
        self.client.force_login(administrator)
        response = self.client.get('/warehouse/')
        self.assertEqual(response.status_code, 200)

    def test_system_administrator_can_open_permissions_and_create_employee(self):
        admin = User.objects.create_superuser(username='role-admin', password='password123', email='')
        self.client.force_login(admin)
        response = self.client.get('/permissions/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '创建账号')
        response = self.client.post('/permissions/', {
            'action': 'create_user',
            'username': 'new-employee',
            'password': 'password123',
            'employee_no': 'EMP-001',
            'phone': '13800138000',
            'role': 'employee',
        })
        self.assertRedirects(response, '/permissions/')
        self.assertTrue(User.objects.filter(username='new-employee').exists())
        self.assertTrue(User.objects.get(username='new-employee').profile.employee_no == 'EMP-001')

    def test_data_purge_requires_exact_confirmation_and_preserves_current_superuser(self):
        admin = User.objects.create_superuser(username='purge-admin', password='password123', email='')
        self.client.force_login(admin)
        response = self.client.get('/permissions/')
        self.assertContains(response, '请输入：清空所有业务数据')
        self.assertContains(response, '确认并清空')

        response = self.client.post('/permissions/', {
            'action': 'purge_data',
            'confirmation': '错误确认文字',
            'password': 'password123',
        })
        self.assertRedirects(response, '/permissions/')
        self.assertTrue(Asset.objects.exists())

        response = self.client.post('/permissions/', {
            'action': 'purge_data',
            'confirmation': '清空所有业务数据',
            'password': 'password123',
        })
        self.assertRedirects(response, '/permissions/')
        self.assertFalse(Asset.objects.exists())
        self.assertEqual(list(User.objects.values_list('username', flat=True)), ['purge-admin'])

    def test_data_purge_with_empty_confirmation_does_not_delete_data(self):
        admin = User.objects.create_superuser(username='purge-empty-admin', password='password123', email='')
        self.client.force_login(admin)
        response = self.client.post('/permissions/', {
            'action': 'purge_data',
            'confirmation': '',
            'password': 'password123',
        })
        self.assertRedirects(response, '/permissions/')
        self.assertTrue(Asset.objects.exists())
        messages = list(response.wsgi_request._messages)
        self.assertEqual(len(messages), 1)
        self.assertIn('确认文字不正确', str(messages[0]))

    def test_create_employee_rejects_short_password_without_server_error(self):
        admin = User.objects.create_superuser(username='create-admin', password='password123', email='')
        self.client.force_login(admin)
        response = self.client.post('/permissions/', {
            'action': 'create_user',
            'username': 'short-password-user',
            'password': '123',
            'employee_no': 'EMP-SHORT',
        })
        self.assertRedirects(response, '/permissions/')
        self.assertFalse(User.objects.filter(username='short-password-user').exists())

    def test_create_employee_accepts_unselected_department(self):
        admin = User.objects.create_superuser(username='create-department-admin', password='password123', email='')
        self.client.force_login(admin)
        response = self.client.post('/permissions/', {
            'action': 'create_user',
            'username': 'no-department-user',
            'password': 'password123',
            'employee_no': 'EMP-NO-DEPARTMENT',
            'department_id': '',
            'permissions': ['warehouse_entry'],
        })
        self.assertRedirects(response, '/permissions/')
        profile = User.objects.get(username='no-department-user').profile
        self.assertIsNone(profile.department)

    def test_create_employee_rejects_seven_character_password(self):
        admin = User.objects.create_superuser(username='six-password-admin', password='password123', email='')
        self.client.force_login(admin)
        response = self.client.post('/permissions/', {
            'action': 'create_user',
            'username': 'seven-password-user',
            'password': '1234567',
            'employee_no': 'EMP-SIX-PASSWORD',
        })
        self.assertRedirects(response, '/permissions/')
        self.assertFalse(User.objects.filter(username='seven-password-user').exists())

    def test_create_employee_accepts_eight_character_password(self):
        admin = User.objects.create_superuser(username='eight-password-admin', password='password123', email='')
        self.client.force_login(admin)
        response = self.client.post('/permissions/', {
            'action': 'create_user',
            'username': 'eight-password-user',
            'password': '12345678',
            'employee_no': 'EMP-EIGHT-PASSWORD',
        })
        self.assertRedirects(response, '/permissions/')
        self.assertTrue(User.objects.get(username='eight-password-user').check_password('12345678'))

    def test_system_administrator_can_delete_non_superuser_account(self):
        admin = User.objects.create_superuser(username='delete-admin', password='password123', email='')
        target = User.objects.create_user(username='delete-target', password='password123')
        UserProfile.objects.create(user=target, employee_no='EMP-DELETE')
        self.client.force_login(admin)
        response = self.client.post('/permissions/', {
            'action': 'delete_user',
            'user_id': target.id,
        })
        self.assertRedirects(response, '/permissions/')
        self.assertFalse(User.objects.filter(pk=target.pk).exists())
        self.assertTrue(OperationAuditLog.objects.filter(action='user_deleted', target_id=target.pk).exists())

    def test_system_administrator_cannot_delete_self_or_superuser(self):
        admin = User.objects.create_superuser(username='protected-admin', password='password123', email='')
        other_admin = User.objects.create_superuser(username='other-admin', password='password123', email='')
        self.client.force_login(admin)
        for user in (admin, other_admin):
            response = self.client.post('/permissions/', {
                'action': 'delete_user',
                'user_id': user.id,
            })
            self.assertRedirects(response, '/permissions/')
        self.assertTrue(User.objects.filter(pk=admin.pk).exists())
        self.assertTrue(User.objects.filter(pk=other_admin.pk).exists())

    def test_notification_click_marks_read_and_opens_its_workflow_request(self):
        workflow_request = WorkflowRequest.objects.create(
            applicant=self.employee,
            request_type=WorkflowRequest.RequestType.BORROW,
            reason='测试通知跳转',
        )
        notice = Notification.objects.create(
            recipient=self.employee,
            title='待审批通知',
            content='请查看申请单。',
            related_model='WorkflowRequest',
            related_object_id=str(workflow_request.id),
        )
        self.client.force_login(self.employee)
        response = self.client.get(f'/notifications/{notice.id}/open/')
        self.assertRedirects(response, f'/workflow/{workflow_request.id}/')
        notice.refresh_from_db()
        self.assertTrue(notice.is_read)

    def test_dashboard_notification_links_to_its_related_request(self):
        workflow_request = WorkflowRequest.objects.create(
            applicant=self.employee,
            request_type=WorkflowRequest.RequestType.BORROW,
            reason='工作台通知跳转',
        )
        notice = Notification.objects.create(
            recipient=self.employee,
            title='申请状态更新',
            content='请查看申请单。',
            related_model='WorkflowRequest',
            related_object_id=str(workflow_request.id),
        )
        self.client.force_login(self.employee)

        response = self.client.get('/')

        self.assertContains(response, f'/notifications/{notice.id}/open/')

    def test_asset_admin_page_is_available_and_chinese(self):
        user = User.objects.create_superuser(username='admin', password='admin', email='')
        client = Client()
        self.assertTrue(client.login(username='admin', password='admin'))

        response = client.get('/admin/inventory/asset/add/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '售后仓库管理系统')
        self.assertContains(response, '资产名称')
        self.assertContains(response, '当前登录：')
        self.assertContains(response, '返回工作台')

    def test_asset_column_preferences_are_saved_per_user(self):
        user = User.objects.create_superuser(username='asset-admin', password='admin', email='')
        client = Client()
        self.assertTrue(client.login(username='asset-admin', password='admin'))

        response = client.post('/admin/inventory/asset/columns/', {
            'columns': ['asset_code', 'name', 'status'],
            'order_asset_code': '1',
            'order_name': '2',
            'order_status': '3',
        })
        self.assertRedirects(response, '/admin/inventory/asset/')
        preference = UserListPreference.objects.get(user=user, list_key='inventory.asset.columns')
        self.assertEqual(preference.columns, ['system_asset_no', 'asset_code', 'name', 'status'])

    def test_admin_can_open_selected_asset_qr_label_print_page(self):
        user = User.objects.create_superuser(username='label-admin', password='admin', email='')
        client = Client()
        self.assertTrue(client.login(username='label-admin', password='admin'))
        session = client.session
        session['inventory_asset_label_ids'] = [str(self.asset.id)]
        session.save()

        response = client.get('/admin/inventory/asset/print-labels/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.asset.asset_code)
        self.assertContains(response, '打印二维码标签')

    def test_item_type_bulk_category_page_updates_types(self):
        user = User.objects.create_superuser(username='category-admin', password='admin', email='')
        client = Client()
        self.assertTrue(client.login(username='category-admin', password='admin'))
        new_category = Category.objects.create(name='机器人本体', code='ROBOT')
        item_type = ItemType.objects.get(pk=self.asset.item_type_id)

        response = client.post('/admin/inventory/itemtype/bulk-category/', {
            f'category_{item_type.id}': str(new_category.id),
        })
        self.assertRedirects(response, '/admin/inventory/itemtype/')
        item_type.refresh_from_db()
        self.assertEqual(item_type.category, new_category)

    def test_mobile_pwa_shell_resources_are_available(self):
        worker = self.client.get('/sw.js')
        manifest_path = finders.find('warehouse/manifest.webmanifest')

        self.assertEqual(worker.status_code, 200)
        self.assertEqual(worker['Content-Type'].split(';')[0], 'application/javascript')
        self.assertContains(worker, 'skipWaiting')
        self.assertContains(worker, 'warehouse-static-v4')
        self.assertContains(worker, 'cache.addAll(STATIC_ASSETS)')
        self.assertNotIn(b'const PAGE_CACHE', worker.content)
        self.assertNotIn(b"request.mode === 'navigate'", worker.content)
        self.assertIsNotNone(manifest_path)

    def test_authenticated_pages_include_offline_user_identity(self):
        self.client.force_login(self.employee)
        response = self.client.get('/')

        self.assertContains(response, f'data-user-id="{self.employee.pk}"')


class ImmutableAuditTests(TestCase):
    """审计留痕只增不改约束：L-02 / L-03 验收场景。"""

    def setUp(self):
        self.actor = User.objects.create_user(username='auditor', password='password123')

    def _make_log(self):
        return OperationAuditLog.objects.create(
            actor=self.actor,
            action='test_action',
            target_model='Asset',
            summary='测试留痕',
        )

    def test_audit_log_can_be_created(self):
        log = self._make_log()
        self.assertIsNotNone(log.pk)

    def test_audit_log_update_is_rejected(self):
        log = self._make_log()
        log.summary = '篡改后的摘要'
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            log.save()
        log.refresh_from_db()
        self.assertEqual(log.summary, '测试留痕')

    def test_audit_log_delete_is_rejected(self):
        log = self._make_log()
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            log.delete()
        self.assertTrue(OperationAuditLog.objects.filter(pk=log.pk).exists())

    def test_lifecycle_event_update_and_delete_rejected(self):
        from apps.inventory.models import AssetLifecycleEvent
        asset = Asset.objects.create(
            name='审计资产',
            item_type=ItemType.objects.create(
                name='类型', code='T',
                category=Category.objects.create(name='分类', code='C'),
            ),
            warehouse=Warehouse.objects.create(name='仓', code='W'),
        )
        event = AssetLifecycleEvent.objects.create(
            asset=asset,
            event_type=AssetLifecycleEvent.EventType.NOTE,
            title='原始备注',
            actor=self.actor,
        )
        from django.core.exceptions import ValidationError
        event.title = '篡改'
        with self.assertRaises(ValidationError):
            event.save()
        with self.assertRaises(ValidationError):
            event.delete()
        event.refresh_from_db()
        self.assertEqual(event.title, '原始备注')


class AuditLogListTests(TestCase):
    """管理员审计检索页：只读、可筛选、普通员工不可见。"""

    def setUp(self):
        self.admin = User.objects.create_superuser(
            username='audit-admin', password='password123', email=''
        )
        self.employee = User.objects.create_user(username='audit-employee', password='password123')
        self.target_id = uuid.uuid4()
        OperationAuditLog.objects.create(
            actor=self.admin,
            action='asset_updated',
            target_model='Asset',
            target_id=self.target_id,
            summary='更新机器人基础信息',
            before_data={'状态': '在库'},
            after_data={'状态': '维修中'},
        )
        OperationAuditLog.objects.create(
            actor=self.employee,
            action='request_created',
            target_model='WorkflowRequest',
            summary='提交借用申请',
        )

    def test_non_admin_cannot_view_audit_logs(self):
        self.client.force_login(self.employee)
        response = self.client.get('/system/audit/', follow=True)

        self.assertEqual(response.redirect_chain[-1][0], '/')
        self.assertContains(response, '当前账号没有审计日志查看权限。')

    def test_admin_can_filter_by_keyword_and_view_change_details(self):
        self.client.force_login(self.admin)
        response = self.client.get('/system/audit/', {'q': '机器人'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '审计日志')
        self.assertContains(response, 'asset_updated')
        self.assertContains(response, '更新机器人基础信息')
        self.assertContains(response, '维修中')
        self.assertNotContains(response, '提交借用申请')

    def test_admin_can_filter_by_target_id_and_invalid_id_returns_empty(self):
        self.client.force_login(self.admin)
        response = self.client.get('/system/audit/', {'target_id': str(self.target_id)})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '更新机器人基础信息')
        self.assertNotContains(response, '提交借用申请')

        response = self.client.get('/system/audit/', {'target_id': 'not-a-uuid'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '没有匹配的审计记录')


class StaleInventoryReportTests(TestCase):
    """呆滞库存只读分析：当前在库、阈值计算、权限与导出。"""

    def setUp(self):
        self.admin = User.objects.create_superuser(
            username='stale-admin', password='password123', email=''
        )
        self.employee = User.objects.create_user(username='stale-employee', password='password123')
        category = Category.objects.create(name='呆滞分类', code='STALE-CATEGORY')
        item_type = ItemType.objects.create(
            name='呆滞类型', code='STALE-TYPE', category=category,
        )
        warehouse = Warehouse.objects.create(name='呆滞测试仓', code='STALE-WH')
        location = Location.objects.create(
            warehouse=warehouse, name='呆滞货架', code='STALE-01',
        )
        old_date = timezone.localdate() - timedelta(days=120)
        self.old_asset = Asset.objects.create(
            name='长期未动资产',
            item_type=item_type,
            warehouse=warehouse,
            location=location,
            asset_code='STALE-ASSET-001',
            received_date=old_date,
            purchase_amount='100.00',
        )
        self.fresh_asset = Asset.objects.create(
            name='近期活动资产',
            item_type=item_type,
            warehouse=warehouse,
            location=location,
            asset_code='STALE-ASSET-002',
            received_date=timezone.localdate(),
        )
        self.old_stock = StockItem.objects.create(
            name='长期未动配件',
            code='STALE-STOCK-001',
            item_type=item_type,
            warehouse=warehouse,
            location=location,
            quantity=5,
            unit='个',
            unit_cost='20.00',
        )
        StockItem.objects.filter(pk=self.old_stock.pk).update(
            created_at=timezone.now() - timedelta(days=120),
        )

    def test_report_filters_by_idle_days_and_exports_cost_value(self):
        self.client.force_login(self.admin)
        response = self.client.get('/reports/stale-inventory/', {'days': '90'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '长期未动资产')
        self.assertContains(response, '长期未动配件')
        self.assertNotContains(response, '近期活动资产')
        self.assertContains(response, '200.00')

        export = self.client.get('/reports/stale-inventory.csv', {'days': '90'})
        self.assertEqual(export.status_code, 200)
        content = export.content.decode('utf-8-sig')
        self.assertIn('长期未动资产', content)
        self.assertIn('长期未动配件', content)
        self.assertIn('库存成本价值（元）', content)

    def test_report_supports_keyword_and_invalid_days_falls_back(self):
        self.client.force_login(self.admin)
        response = self.client.get('/reports/stale-inventory/', {
            'q': '长期未动配件', 'days': '不是数字',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '长期未动配件')
        self.assertNotContains(response, '长期未动资产')
        self.assertContains(response, '超过多少天')

    def test_regular_employee_cannot_view_stale_inventory_report(self):
        self.client.force_login(self.employee)
        response = self.client.get('/reports/stale-inventory/', follow=True)

        self.assertEqual(response.redirect_chain[-1][0], '/')
        self.assertContains(response, '当前账号没有报表查看权限。')


class ConsistencyCheckTests(TestCase):
    """数据一致性检查：异常发现、导出和系统管理员权限。"""

    def setUp(self):
        self.admin = User.objects.create_superuser(
            username='consistency-admin', password='password123', email=''
        )
        self.employee = User.objects.create_user(
            username='consistency-employee', password='password123',
        )
        category = Category.objects.create(name='一致性分类', code='CONSISTENCY-CATEGORY')
        item_type = ItemType.objects.create(
            name='一致性类型', code='CONSISTENCY-TYPE', category=category,
        )
        self.warehouse = Warehouse.objects.create(name='一致性仓', code='CONSISTENCY-WH')
        other_warehouse = Warehouse.objects.create(name='另一个仓', code='CONSISTENCY-WH-2')
        self.location = Location.objects.create(
            warehouse=self.warehouse, name='一致性库位', code='CONSISTENCY-01',
        )
        wrong_location = Location.objects.create(
            warehouse=other_warehouse, name='错误库位', code='CONSISTENCY-02',
        )
        Asset.objects.create(
            name='库位异常资产',
            item_type=item_type,
            warehouse=self.warehouse,
            location=wrong_location,
            asset_code='CONSISTENCY-ASSET-001',
            serial_number='DUPLICATE-SN',
        )
        Asset.objects.create(
            name='重复 SN 资产',
            item_type=item_type,
            warehouse=self.warehouse,
            location=self.location,
            asset_code='CONSISTENCY-ASSET-002',
            serial_number='DUPLICATE-SN',
        )
        self.stock_item = StockItem.objects.create(
            name='预占异常配件',
            code='CONSISTENCY-STOCK-001',
            item_type=item_type,
            warehouse=self.warehouse,
            location=self.location,
            quantity=2,
            unit='个',
        )
        workflow_request = WorkflowRequest.objects.create(
            applicant=self.admin,
            request_type=WorkflowRequest.RequestType.BORROW,
        )
        line = WorkflowRequestLine.objects.create(
            request=workflow_request,
            stock_item=self.stock_item,
            quantity=1,
        )
        InventoryReservation.objects.create(
            request_line=line,
            stock_item=self.stock_item,
            quantity=5,
            status=InventoryReservation.Status.ACTIVE,
        )
        StockItemHolding.objects.create(
            stock_item=self.stock_item,
            holder=self.admin,
            quantity=4,
        )

    def test_admin_can_view_and_export_consistency_issues(self):
        self.client.force_login(self.admin)
        response = self.client.get('/system/consistency/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '数据一致性检查')
        self.assertContains(response, 'location_warehouse_mismatch')
        self.assertContains(response, 'duplicate_serial_number')
        self.assertContains(response, 'reserved_quantity_exceeds_stock')
        self.assertContains(response, 'holding_quantity_exceeds_stock')
        self.assertContains(response, '处理建议')
        self.assertContains(response, '核对资产实物标签')

        export = self.client.get('/system/consistency.csv')
        self.assertEqual(export.status_code, 200)
        content = export.content.decode('utf-8-sig')
        self.assertIn('问题代码', content)
        self.assertIn('处理建议', content)
        self.assertIn('reserved_quantity_exceeds_stock', content)

    def test_consistency_check_filters_by_severity_and_keyword(self):
        self.client.force_login(self.admin)
        response = self.client.get('/system/consistency/', {
            'severity': 'error',
            'q': '预占',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'reserved_quantity_exceeds_stock')
        self.assertNotContains(response, 'in_stock_with_holder')

    def test_consistency_check_detects_asset_status_dimension_mismatch(self):
        asset = Asset.objects.filter(serial_number='DUPLICATE-SN').first()
        Asset.objects.filter(pk=asset.pk).update(
            availability_state=Asset.AvailabilityState.FROZEN,
        )
        self.client.force_login(self.admin)

        response = self.client.get('/system/consistency/', {'q': asset.system_asset_no})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'asset_status_dimensions_mismatch')
        self.assertContains(response, '可用性应为')

    def test_consistency_check_detects_invalid_asset_state_combination(self):
        asset = Asset.objects.filter(serial_number='DUPLICATE-SN').first()
        Asset.objects.filter(pk=asset.pk).update(
            location_state=Asset.LocationState.OUT_ON_LOAN,
            availability_state=Asset.AvailabilityState.AVAILABLE,
            quality_state=Asset.QualityState.NORMAL,
            disposition_state=Asset.DispositionState.INTERNAL,
        )
        self.client.force_login(self.admin)

        response = self.client.get('/system/consistency/', {'q': asset.system_asset_no})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'asset_state_combination_invalid')
        self.assertContains(response, '四维状态组合不受支持')

    def test_non_admin_cannot_view_consistency_check(self):
        self.client.force_login(self.employee)
        response = self.client.get('/system/consistency/', follow=True)

        self.assertEqual(response.redirect_chain[-1][0], '/')
        self.assertContains(response, '当前账号没有数据一致性检查权限。')

    @patch('apps.common.management.commands.check_data_consistency.send_group_message', return_value=True)
    def test_consistency_command_notifies_only_on_first_change_and_recovery(self, send_group_message):
        import tempfile
        from pathlib import Path
        from django.core.management import call_command

        with tempfile.TemporaryDirectory() as directory:
            state_file = str(Path(directory) / 'consistency.json')
            call_command('check_data_consistency', '--notify', '--state-file', state_file)
            call_command('check_data_consistency', '--notify', '--state-file', state_file)
            self.assertEqual(send_group_message.call_count, 1)

            Asset.objects.filter(serial_number='DUPLICATE-SN').first().save(
                update_fields=['serial_number'],
            )
            # 上面的 save 没有改变内容，确保状态保持去重。
            call_command('check_data_consistency', '--notify', '--state-file', state_file)
            self.assertEqual(send_group_message.call_count, 1)

            changed_asset = Asset.objects.filter(serial_number='DUPLICATE-SN').first()
            changed_asset.serial_number = 'CHANGED-SN'
            changed_asset.save(update_fields=['serial_number'])
            call_command('check_data_consistency', '--notify', '--state-file', state_file)
            self.assertEqual(send_group_message.call_count, 2)

            InventoryReservation.objects.all().delete()
            StockItemHolding.objects.all().delete()
            Asset.objects.all().delete()
            call_command('check_data_consistency', '--notify', '--state-file', state_file)
            self.assertEqual(send_group_message.call_count, 3)
            self.assertIn('已恢复', send_group_message.call_args.args[0])

    @patch('apps.common.management.commands.check_data_consistency.send_group_message', return_value=False)
    def test_consistency_command_does_not_save_state_when_notification_fails(self, send_group_message):
        import tempfile
        from pathlib import Path
        from django.core.management import call_command

        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / 'consistency.json'
            call_command('check_data_consistency', '--notify', '--state-file', str(state_path))
            self.assertFalse(state_path.exists())
            self.assertTrue(send_group_message.called)


class ListPreferenceTests(TestCase):
    """列表展示偏好保存/读取：P2-8 功能。"""

    def setUp(self):
        self.user = User.objects.create_user(username='pref-user', password='password123')
        self.client = Client()
        self.client.login(username='pref-user', password='password123')

    def test_save_page_size_preference(self):
        response = self.client.post('/preferences/list/', {
            'list_key': 'warehouse_management',
            'page_size': '200',
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])
        pref = UserListPreference.objects.get(user=self.user, list_key='warehouse_management')
        self.assertEqual(pref.columns['page_size'], 200)

    def test_save_columns_preference(self):
        response = self.client.post('/preferences/list/', {
            'list_key': 'warehouse_management',
            'columns': 'name,code,status',
        })
        self.assertEqual(response.status_code, 200)
        pref = UserListPreference.objects.get(user=self.user, list_key='warehouse_management')
        self.assertEqual(pref.columns['columns'], ['name', 'code', 'status'])

    def test_reject_invalid_page_size(self):
        response = self.client.post('/preferences/list/', {
            'list_key': 'warehouse_management',
            'page_size': '999',
        })
        self.assertEqual(response.status_code, 400)

    def test_reject_get_method(self):
        response = self.client.get('/preferences/list/')
        self.assertEqual(response.status_code, 405)

    def test_reject_missing_list_key(self):
        response = self.client.post('/preferences/list/', {'page_size': '100'})
        self.assertEqual(response.status_code, 400)

    def test_preference_isolated_per_user(self):
        other = User.objects.create_user(username='other', password='password123')
        UserListPreference.objects.create(user=other, list_key='k', columns={'page_size': 50})
        UserListPreference.objects.create(user=self.user, list_key='k', columns={'page_size': 200})
        self.assertEqual(
            UserListPreference.objects.get(user=self.user, list_key='k').columns['page_size'], 200,
        )

    def test_requires_login(self):
        Client().post('/preferences/list/', {'list_key': 'k', 'page_size': '50'})
        self.assertFalse(UserListPreference.objects.filter(list_key='k').exists())


class ExportAuditArchiveTests(TestCase):
    """审计归档导出命令：L 段数据安全。"""

    def setUp(self):
        self.actor = User.objects.create_user(username='archiver', password='password123')

    def test_export_produces_tarball_with_checksums(self):
        import io
        import tarfile
        import tempfile
        from pathlib import Path
        from django.core.management import call_command

        OperationAuditLog.objects.create(
            actor=self.actor, action='create', target_model='Asset', summary='测试',
        )
        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            call_command('export_audit_archive', '--output', tmp, stdout=out)
            archives = list(Path(tmp).glob('audit-archive-*.tar.gz'))
            self.assertEqual(len(archives), 1)
            with tarfile.open(archives[0]) as tar:
                names = tar.getnames()
                self.assertIn('operation_audit_log.jsonl', names)
                self.assertIn('asset_lifecycle_event.jsonl', names)
                self.assertIn('SHA256SUMS', names)
                # JSONL 内容可解析
                member = tar.extractfile('operation_audit_log.jsonl')
                import json
                rows = [json.loads(line) for line in member.read().decode().splitlines() if line.strip()]
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]['data']['action'], 'create')

    def test_export_before_filter(self):
        import io
        import tempfile
        from pathlib import Path
        from django.core.management import call_command
        from django.utils import timezone
        from datetime import timedelta

        old = OperationAuditLog.objects.create(
            actor=self.actor, action='old', target_model='Asset',
        )
        # 把 created_at 改到 10 天前（绕过 auto_now_add 用 queryset update）
        past = timezone.now() - timedelta(days=10)
        OperationAuditLog.objects.filter(pk=old.pk).update(created_at=past)

        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            boundary = (timezone.now() - timedelta(days=5)).strftime('%Y-%m-%d')
            call_command('export_audit_archive', '--output', tmp, '--before', boundary, stdout=out)
            archives = list(Path(tmp).glob('audit-archive-*.tar.gz'))
            self.assertEqual(len(archives), 1)
            import tarfile, json
            with tarfile.open(archives[0]) as tar:
                rows = [json.loads(l) for l in tar.extractfile('operation_audit_log.jsonl').read().decode().splitlines() if l.strip()]
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]['data']['action'], 'old')
