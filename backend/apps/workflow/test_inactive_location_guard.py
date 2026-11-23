"""停用仓库/库位的写入拦截回归（P2-02）。

历史行为：服务层只校验「库位属于仓库」，is_active 只在表单 queryset 过滤，
旧书签/离线队列/直接 API 调用仍可把库存写进已停用的位置。
"""

from datetime import date, timedelta

from django.test import TestCase

from apps.common.testing.fixtures import WarehouseTestFixture
from apps.inventory.models import Location, StockItem
from apps.workflow.models import PurchaseOrder, PurchaseRequest, WorkflowRequest, WorkflowRequestLine
from apps.workflow.services import (
    WorkflowError,
    approve_purchase_request,
    create_purchase_order,
    place_purchase_order,
    quick_process_request,
    receive_purchase_order,
)


class InactiveLocationGuardTests(WarehouseTestFixture, TestCase):
    def setUp(self):
        self.warehouse = self.make_warehouse()
        self.location = Location.objects.create(
            warehouse=self.warehouse, name='A-01', code='A-01',
        )
        self.executor = self.make_user('inactive-exec', groups=('warehouse_outbound',))

    def test_receive_into_inactive_location_is_rejected(self):
        order = self._ordered(quantity=5)
        self.location.is_active = False
        self.location.save(update_fields=['is_active'])
        with self.assertRaisesMessage(WorkflowError, '已停用'):
            receive_purchase_order(
                order, self.executor, {str(order.lines.get().id): 1},
                self.warehouse, self.location,
            )

    def test_receive_into_inactive_warehouse_is_rejected(self):
        order = self._ordered(quantity=5)
        self.warehouse.is_active = False
        self.warehouse.save(update_fields=['is_active'])
        with self.assertRaisesMessage(WorkflowError, '已停用'):
            receive_purchase_order(
                order, self.executor, {str(order.lines.get().id): 1},
                self.warehouse, self.location,
            )

    def test_inactive_location_is_rejected_but_inventory_unchanged(self):
        order = self._ordered(quantity=5, stock_quantity=10)
        self.location.is_active = False
        self.location.save(update_fields=['is_active'])
        with self.assertRaises(WorkflowError):
            receive_purchase_order(
                order, self.executor, {str(order.lines.get().id): 2},
                self.warehouse, self.location,
            )
        self.assertEqual(StockItem.objects.get(code='GUARD-STOCK').quantity, 10)

    def test_quick_process_into_inactive_location_is_rejected(self):
        stock = StockItem.objects.create(
            name='快速耗材', code='QUICK-GUARD', quantity=5,
            warehouse=self.warehouse, location=self.location,
        )
        quick_request = self._quick_request(stock)
        self.location.is_active = False
        self.location.save(update_fields=['is_active'])
        with self.assertRaisesMessage(WorkflowError, '已停用'):
            quick_process_request(quick_request, self.executor)

    def _ordered(self, *, quantity, stock_quantity=10):
        self.stock = StockItem.objects.create(
            name='守卫耗材', code='GUARD-STOCK', quantity=stock_quantity,
            warehouse=self.warehouse, location=self.location,
        )
        applicant = self.make_employee('inactive-applicant')
        approver = self.make_user('inactive-approver', groups=('warehouse_approval',))
        manager = self.make_user('inactive-buyer', groups=('warehouse_entry',))
        purchase_request = PurchaseRequest.objects.create(
            applicant=applicant, reason='停用库位回归',
            status=PurchaseRequest.Status.PENDING_APPROVAL,
        )
        purchase_request.lines.create(
            stock_item=self.stock, name_snapshot=self.stock.name,
            code_snapshot=self.stock.code, management_mode='耗材配件',
            quantity=quantity, unit='个', reference_unit_cost='10.00',
            supplier_snapshot='守卫供应商',
        )
        approve_purchase_request(purchase_request, approver, '同意')
        order = create_purchase_order(purchase_request, manager)
        place_purchase_order(order, manager, date.today() + timedelta(days=7), '下达')
        return order

    def _quick_request(self, stock):
        request_obj = WorkflowRequest.objects.create(
            request_type=WorkflowRequest.RequestType.ISSUE,
            applicant=self.executor,
            reason='快速出入库停用库位回归',
            recipient_name='张三', recipient_phone='13800000000',
            usage_location='测试地点', contact_source='电话',
            is_quick_process=True,
            status=WorkflowRequest.Status.DRAFT,
            application_date=date(2026, 11, 20),
            transaction_date=date(2026, 11, 20),
            target_warehouse=self.warehouse,
            target_location=self.location,
        )
        WorkflowRequestLine.objects.create(request=request_obj, stock_item=stock, quantity=1)
        return request_obj
