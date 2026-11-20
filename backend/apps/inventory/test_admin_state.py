from django.test import TestCase

from datetime import timedelta

from apps.common.models import OperationAuditLog
from apps.common.testing.fixtures import WarehouseTestFixture

from .models import Asset, AssetLifecycleEvent, AssetStateCorrection, Location
from .state_services import AssetStateCorrectionError, correct_asset_states


class AdminStatusAdjustmentTests(WarehouseTestFixture, TestCase):
    def setUp(self):
        # 2026-11-20 收紧：终态（已售出/已退供应商/已报废）只能走业务单据，
        # 仅 is_superuser 可作历史补录例外。warehouse_admin 组不再拥有该能力。
        self.admin = self.make_user('state-admin', groups=('warehouse_admin',))
        self.superadmin = self.make_user('state-superadmin', groups=('warehouse_admin',))
        self.superadmin.is_superuser = True
        self.superadmin.save(update_fields=['is_superuser'])
        self.clerk = self.make_user('state-clerk', groups=('warehouse_state_correction',))
        self.warehouse = self.make_warehouse()
        self.location = Location.objects.create(warehouse=self.warehouse, code='S-01', name='S-01')

    def test_superadmin_can_mark_asset_sold(self):
        """终态例外通道：仅超级管理员可在历史补录时直接写入。"""
        asset = self.make_asset(status=Asset.Status.IN_STOCK)
        correct_asset_states(
            assets=[asset],
            to_status=Asset.Status.SOLD,
            reason='客户已签收，台账补记。',
            actor=self.superadmin,
            warehouse=self.warehouse,
            location=self.location,
        )
        asset.refresh_from_db()
        self.assertEqual(asset.status, Asset.Status.SOLD)
        self.assertEqual(asset.disposition_state, Asset.DispositionState.SOLD)

    def test_admin_group_cannot_mark_asset_sold(self):
        """warehouse_admin 组不得绕过出售单据直接标记已售出。"""
        asset = self.make_asset(status=Asset.Status.IN_STOCK)
        with self.assertRaises(AssetStateCorrectionError) as ctx:
            correct_asset_states(
                assets=[asset],
                to_status=Asset.Status.SOLD,
                reason='管理员试图直接标记已售出',
                actor=self.admin,
                warehouse=self.warehouse,
                location=self.location,
            )
        self.assertIn('最终状态', str(ctx.exception))

    def test_non_admin_cannot_mark_asset_sold(self):
        asset = self.make_asset(status=Asset.Status.IN_STOCK)
        with self.assertRaises(AssetStateCorrectionError):
            correct_asset_states(
                assets=[asset],
                to_status=Asset.Status.SOLD,
                reason='越权尝试',
                actor=self.clerk,
            )

    def test_admin_cannot_switch_to_in_progress_status(self):
        asset = self.make_asset(status=Asset.Status.IN_STOCK)
        with self.assertRaises(AssetStateCorrectionError):
            correct_asset_states(
                assets=[asset],
                to_status=Asset.Status.BORROWED,
                reason='绕过流程',
                actor=self.admin,
            )

    def test_admin_cannot_restore_terminal_asset_by_direct_correction(self):
        asset = self.make_asset(status=Asset.Status.SOLD)

        with self.assertRaisesMessage(AssetStateCorrectionError, '冲销或反向业务单据'):
            correct_asset_states(
                assets=[asset],
                to_status=Asset.Status.IN_STOCK,
                reason='不能绕过反向业务单据',
                actor=self.admin,
                warehouse=self.warehouse,
                location=self.location,
            )

        asset.refresh_from_db()
        self.assertEqual(asset.status, Asset.Status.SOLD)
        self.assertFalse(AssetStateCorrection.objects.filter(asset=asset).exists())

class AdminStateAdjustmentPageTests(WarehouseTestFixture, TestCase):
    def setUp(self):
        # 2026-11-20 收紧后，台账校正入口需要 warehouse_state_correction；
        # 终态（SOLD/RETURNED_TO_VENDOR/SCRAPPED）只有 superuser 可写入。
        # 本类验证的是「页面提交链路」与「并发版本控制」，与具体目标状态无关，
        # 因此改用非终态 DAMAGED。
        self.admin = self.make_user(
            'page-admin',
            groups=('warehouse_admin', 'warehouse_state_correction'),
        )
        self.superadmin = self.make_user('page-superadmin', groups=('warehouse_admin',))
        self.superadmin.is_superuser = True
        self.superadmin.save(update_fields=['is_superuser'])
        self.warehouse = self.make_warehouse()
        self.location = Location.objects.create(warehouse=self.warehouse, code='P-01', name='P-01')
        self.asset = self.make_asset(status=Asset.Status.IN_STOCK, warehouse=self.warehouse)

    def test_superadmin_can_submit_terminal_status(self):
        """终态走例外通道：superuser 可在生命周期页直接写入。"""
        self.client.force_login(self.superadmin)
        response = self.client.post(
            f'/warehouse/assets/{self.asset.id}/lifecycle/?tab=corrections',
            {
                'action': 'correct_state',
                'correction-to_status': Asset.Status.SOLD,
                'correction-source': 'legacy_review',
                'correction-target_warehouse': str(self.warehouse.id),
                'correction-target_location': str(self.location.id),
                'correction-reason': '历史补录：源表记录已售出。',
                'correction-expected_asset_updated_at': self.asset.updated_at.isoformat(),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, Asset.Status.SOLD)

    def test_admin_cannot_submit_terminal_status(self):
        """warehouse_admin + state_correction 也不得直接写入终态。"""
        self.client.force_login(self.admin)
        response = self.client.post(
            f'/warehouse/assets/{self.asset.id}/lifecycle/?tab=corrections',
            {
                'action': 'correct_state',
                'correction-to_status': Asset.Status.SOLD,
                'correction-source': 'legacy_review',
                'correction-target_warehouse': str(self.warehouse.id),
                'correction-target_location': str(self.location.id),
                'correction-reason': '管理员试图直接标记已售出。',
                'correction-expected_asset_updated_at': self.asset.updated_at.isoformat(),
            },
        )
        # 表单层会拒绝该选项（choices 不含终态），页面返回 200 并保留原状态
        self.assertEqual(response.status_code, 200)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, Asset.Status.IN_STOCK)

    def test_lifecycle_page_can_submit_damaged_status(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            f'/warehouse/assets/{self.asset.id}/lifecycle/?tab=corrections',
            {
                'action': 'correct_state',
                'correction-to_status': Asset.Status.DAMAGED,
                'correction-source': 'legacy_review',
                'correction-target_warehouse': str(self.warehouse.id),
                'correction-target_location': str(self.location.id),
                'correction-reason': '源表记录错误，实物已售出。',
                'correction-expected_asset_updated_at': self.asset.updated_at.isoformat(),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, Asset.Status.DAMAGED)

    def test_lifecycle_page_rejects_stale_asset_version(self):
        stale_updated_at = self.asset.updated_at
        concurrent_updated_at = stale_updated_at + timedelta(seconds=1)
        Asset.objects.filter(pk=self.asset.pk).update(
            name='并发修改后的资产',
            updated_at=concurrent_updated_at,
        )
        self.client.force_login(self.admin)

        response = self.client.post(
            f'/warehouse/assets/{self.asset.id}/lifecycle/?tab=corrections',
            {
                'action': 'correct_state',
                'correction-to_status': Asset.Status.DAMAGED,
                'correction-source': 'legacy_review',
                'correction-target_warehouse': str(self.warehouse.id),
                'correction-target_location': str(self.location.id),
                'correction-reason': '旧页面尝试修改状态。',
                'correction-expected_asset_updated_at': stale_updated_at.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '已被其他账号修改，本次未执行')
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.name, '并发修改后的资产')
        self.assertEqual(self.asset.status, Asset.Status.IN_STOCK)
        self.assertEqual(self.asset.updated_at, concurrent_updated_at)
        self.assertFalse(AssetStateCorrection.objects.filter(asset=self.asset).exists())
        self.assertFalse(OperationAuditLog.objects.filter(
            action='asset_state_corrected', target_id=self.asset.id,
        ).exists())
        self.assertFalse(AssetLifecycleEvent.objects.filter(
            asset=self.asset,
            event_type=AssetLifecycleEvent.EventType.CORRECTION,
        ).exists())

    def test_lifecycle_page_requires_asset_version_token(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            f'/warehouse/assets/{self.asset.id}/lifecycle/?tab=corrections',
            {
                'action': 'correct_state',
                'correction-to_status': Asset.Status.DAMAGED,
                'correction-source': 'legacy_review',
                'correction-target_warehouse': str(self.warehouse.id),
                'correction-target_location': str(self.location.id),
                'correction-reason': '缺少页面版本信息。',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '资产版本信息缺失，请刷新页面后重新填写')
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, Asset.Status.IN_STOCK)
        self.assertFalse(AssetStateCorrection.objects.filter(asset=self.asset).exists())
