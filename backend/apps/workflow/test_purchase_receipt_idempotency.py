"""采购到货幂等回归（P1-03）。

历史行为：同参数重放会把同一批货重复入库（探针实测库存 12 → 14），
因为服务层只校验订单状态，部分到货时订单仍是 PARTIAL_RECEIVED。
本文件锁定「同一 client_operation_id 只入库一次」的期望行为。
"""

from datetime import date, timedelta
from uuid import uuid4

from django.test import TestCase

from apps.common.testing.fixtures import WarehouseTestFixture
from apps.inventory.models import Location, StockItem
from apps.workflow.models import (
    InventoryTransaction,
    PurchaseReceipt,
    PurchaseRequest,
    WorkflowRequest,
)
from apps.workflow.services import (
    approve_purchase_request,
    create_purchase_order,
    place_purchase_order,
    receive_purchase_order,
)


class PurchaseReceiptIdempotencyTests(WarehouseTestFixture, TestCase):
    def setUp(self):
        self.warehouse = self.make_warehouse()
        self.location = Location.objects.create(
            warehouse=self.warehouse, name='A-01', code='A-01',
        )
        self.manager = self.make_user('idem-buyer', groups=('warehouse_entry',))
        self.approver = self.make_user('idem-approver', groups=('warehouse_approval',))
        self.executor = self.make_user('idem-exec', groups=('warehouse_outbound',))
        self.employee = self.make_employee('idem-applicant')
        self.stock = StockItem.objects.create(
            name='幂等耗材', code='IDEM-STOCK-1', quantity=10,
            warehouse=self.warehouse, location=self.location,
        )
        self.order = self._ordered(quantity=5)
        self.line_id = str(self.order.lines.get().id)

    def _ordered(self, *, quantity):
        purchase_request = PurchaseRequest.objects.create(
            applicant=self.employee, reason='幂等回归',
            status=PurchaseRequest.Status.PENDING_APPROVAL,
        )
        purchase_request.lines.create(
            stock_item=self.stock, name_snapshot=self.stock.name,
            code_snapshot=self.stock.code, management_mode='耗材配件',
            quantity=quantity, unit='个', reference_unit_cost='10.00',
            supplier_snapshot='幂等供应商',
        )
        approve_purchase_request(purchase_request, self.approver, '同意')
        order = create_purchase_order(purchase_request, self.manager)
        place_purchase_order(order, self.manager, date.today() + timedelta(days=7), '下达')
        return order

    def _receive(self, operation_id, quantity=2):
        return receive_purchase_order(
            self.order, self.executor, {self.line_id: quantity},
            self.warehouse, self.location,
            client_operation_id=operation_id,
        )

    def test_replay_with_same_operation_id_does_not_double_count(self):
        operation_id = uuid4()
        first = self._receive(operation_id)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, 12)

        second = self._receive(operation_id)
        self.stock.refresh_from_db()

        self.assertEqual(self.stock.quantity, 12, '重放不应再次增加库存')
        self.assertEqual(first.pk, second.pk, '重放应返回首次生成的回执')
        self.assertEqual(PurchaseReceipt.objects.filter(order=self.order).count(), 1)
        self.assertEqual(
            InventoryTransaction.objects.filter(
                action=WorkflowRequest.RequestType.PURCHASE_RECEIPT,
            ).count(),
            1,
        )

    def test_different_operation_ids_still_allow_partial_receipts(self):
        self._receive(uuid4(), quantity=2)
        self._receive(uuid4(), quantity=3)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, 15)
        self.assertEqual(PurchaseReceipt.objects.filter(order=self.order).count(), 2)

    def test_replay_after_order_completed_returns_existing_receipt(self):
        """订单到货完毕后重放同一键，应返回原回执而不是报「状态不允许」。"""
        operation_id = uuid4()
        first = self._receive(operation_id, quantity=5)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'received')

        second = self._receive(operation_id, quantity=5)
        self.assertEqual(second.pk, first.pk)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, 15, '重放不应改变库存')

    def test_without_operation_id_behavior_is_unchanged(self):
        """未提供幂等键（历史调用方）时保持原行为，由数据库唯一约束之外的逻辑兜底。"""
        self._receive(None, quantity=2)
        self._receive(None, quantity=2)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, 14)
        self.assertEqual(PurchaseReceipt.objects.filter(order=self.order).count(), 2)
