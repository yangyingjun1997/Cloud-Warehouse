"""PostgreSQL-specific race-condition regression tests.

These tests intentionally use independent database connections. SQLite cannot
prove row-lock behavior, so the suite is skipped unless the test database is
PostgreSQL. Django always runs them against an isolated test database.
"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings

from apps.common.testing.fixtures import WarehouseTestFixture
from apps.inventory.models import Asset, StockItem

from .models import (
    ApprovalLog,
    ApprovalTask,
    InventoryReservation,
    InventoryTransaction,
    WorkflowRequest,
    WorkflowRequestLine,
)
from .services import (
    WorkflowError,
    approve_request,
    quick_process_request,
    submit_request,
)

User = get_user_model()


@skipUnless(connection.vendor == 'postgresql', '需要 PostgreSQL 行锁语义。')
@override_settings(
    DINGTALK_WEBHOOK='',
    DINGTALK_SECRET='',
    DINGTALK_APP_KEY='',
    DINGTALK_APP_SECRET='',
    DINGTALK_AGENT_ID='',
)
class PostgreSQLWorkflowConcurrencyTests(WarehouseTestFixture, TransactionTestCase):
    """Verify that racing writes produce one winner and a controlled loser."""

    reset_sequences = True

    def setUp(self):
        self.employee = self.make_employee('concurrency-employee')
        self.approver_one = self.make_user(
            'concurrency-approver-one', groups=('warehouse_approval',)
        )
        self.approver_two = self.make_user(
            'concurrency-approver-two', groups=('warehouse_approval',)
        )
        self.operator_one = self.make_user(
            'concurrency-operator-one', groups=('warehouse_outbound',)
        )
        self.operator_two = self.make_user(
            'concurrency-operator-two', groups=('warehouse_outbound',)
        )
        self.asset = self.make_asset(name='并发测试资产')
        self.stock_item = StockItem.objects.create(
            name='并发测试耗材',
            code='CONCURRENCY-STOCK',
            item_type=self.asset.item_type,
            warehouse=self.asset.warehouse,
            quantity=4,
        )

    def _race(self, *callbacks):
        barrier = Barrier(len(callbacks))

        def run(callback):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return callback()
            except Exception as exc:  # Returned so the main test connection can assert it.
                return exc
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=len(callbacks)) as executor:
            futures = [executor.submit(run, callback) for callback in callbacks]
            return [future.result(timeout=20) for future in futures]

    def _request(self, *, request_type, asset=None, stock_item=None, quantity=1, quick=False):
        request_obj = WorkflowRequest.objects.create(
            applicant=self.employee,
            request_type=request_type,
            reason='PostgreSQL 并发回归',
            usage_location='并发测试区',
            is_quick_process=quick,
            contact_source='自动化测试' if quick else '',
        )
        WorkflowRequestLine.objects.create(
            request=request_obj,
            asset=asset,
            stock_item=stock_item,
            quantity=quantity,
        )
        return request_obj

    def _assert_one_controlled_failure(self, results):
        self.assertEqual(sum(result == 'ok' for result in results), 1, results)
        failures = [result for result in results if isinstance(result, Exception)]
        self.assertEqual(len(failures), 1, results)
        self.assertIsInstance(failures[0], WorkflowError)

    def test_two_approvers_cannot_decide_the_same_request_twice(self):
        request_obj = self._request(
            request_type=WorkflowRequest.RequestType.BORROW,
            asset=self.asset,
        )
        submit_request(request_obj, self.employee)

        def approve_as(user_id):
            approve_request(
                WorkflowRequest.objects.get(pk=request_obj.pk),
                User.objects.get(pk=user_id),
                '并发同意',
            )
            return 'ok'

        results = self._race(
            lambda: approve_as(self.approver_one.pk),
            lambda: approve_as(self.approver_two.pk),
        )

        self._assert_one_controlled_failure(results)
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.status, WorkflowRequest.Status.WAITING_WAREHOUSE)
        self.assertEqual(
            ApprovalTask.objects.filter(
                request=request_obj,
                status=ApprovalTask.Status.APPROVED,
            ).count(),
            1,
        )
        self.assertEqual(
            ApprovalLog.objects.filter(request=request_obj, action='approve').count(),
            1,
        )

    def test_two_requests_cannot_reserve_the_same_asset(self):
        first = self._request(
            request_type=WorkflowRequest.RequestType.BORROW,
            asset=self.asset,
        )
        second = self._request(
            request_type=WorkflowRequest.RequestType.BORROW,
            asset=self.asset,
        )

        def submit(request_id):
            submit_request(
                WorkflowRequest.objects.get(pk=request_id),
                User.objects.get(pk=self.employee.pk),
            )
            return 'ok'

        results = self._race(lambda: submit(first.pk), lambda: submit(second.pk))

        self._assert_one_controlled_failure(results)
        self.assertEqual(
            WorkflowRequest.objects.filter(
                pk__in=[first.pk, second.pk],
                status=WorkflowRequest.Status.PENDING,
            ).count(),
            1,
        )
        self.assertEqual(
            InventoryReservation.objects.filter(
                asset=self.asset,
                status=InventoryReservation.Status.ACTIVE,
            ).count(),
            1,
        )

    def test_two_quick_operations_cannot_ship_the_same_asset(self):
        first = self._request(
            request_type=WorkflowRequest.RequestType.BORROW,
            asset=self.asset,
            quick=True,
        )
        second = self._request(
            request_type=WorkflowRequest.RequestType.BORROW,
            asset=self.asset,
            quick=True,
        )

        def process(request_id, user_id):
            quick_process_request(
                WorkflowRequest.objects.get(pk=request_id),
                User.objects.get(pk=user_id),
                '并发快速出库',
            )
            return 'ok'

        results = self._race(
            lambda: process(first.pk, self.operator_one.pk),
            lambda: process(second.pk, self.operator_two.pk),
        )

        self._assert_one_controlled_failure(results)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, Asset.Status.BORROWED)
        self.assertEqual(InventoryTransaction.objects.filter(asset=self.asset).count(), 1)
        self.assertEqual(
            WorkflowRequest.objects.filter(
                pk__in=[first.pk, second.pk],
                status=WorkflowRequest.Status.DONE,
            ).count(),
            1,
        )

    def test_parallel_stock_decrements_cannot_oversell(self):
        first = self._request(
            request_type=WorkflowRequest.RequestType.ISSUE,
            stock_item=self.stock_item,
            quantity=3,
            quick=True,
        )
        second = self._request(
            request_type=WorkflowRequest.RequestType.ISSUE,
            stock_item=self.stock_item,
            quantity=3,
            quick=True,
        )

        def process(request_id, user_id):
            quick_process_request(
                WorkflowRequest.objects.get(pk=request_id),
                User.objects.get(pk=user_id),
                '并发耗材出库',
            )
            return 'ok'

        results = self._race(
            lambda: process(first.pk, self.operator_one.pk),
            lambda: process(second.pk, self.operator_two.pk),
        )

        self._assert_one_controlled_failure(results)
        self.stock_item.refresh_from_db()
        self.assertEqual(self.stock_item.quantity, 1)
        self.assertEqual(
            InventoryTransaction.objects.filter(stock_item=self.stock_item).count(),
            1,
        )
