# Project Agents

This repository is organized for handoff-friendly development.

## Working rules

- Keep changes small and traceable.
- Prefer the existing Django app boundaries under `backend/apps/`.
- Put reusable model fields and helpers in `backend/apps/common/`.
- Put business data in `inventory`, workflow logic in `workflow`, identity data in `accounts`, and user-facing alerts in `notifications`.
- Keep API payloads stable and versionable.
- Use Chinese for product-facing text and English for code identifiers.
- Use UUID primary keys for business objects that may be referenced by QR codes or external systems.
- Every state-changing action should be logged.

## Suggested agent roles

- `backend-agent`: Django models, serializers, viewsets, admin, tests.
- `frontend-agent`: Django templates, later Vue or mini-program UI.
- `devops-agent`: Docker, Compose, backup scripts, reverse proxy, Ubuntu setup.
- `qa-agent`: API checks, migration checks, permission checks, smoke tests.

## Current delivery shape

- Backend first.
- Simple management dashboard second.
- Mini-program and richer frontend third.

## Testing conventions

- 共享测试数据用 `apps.common.testing.fixtures.WarehouseTestFixture` 的 `make_*` 工厂，避免每个 TestCase 重复 setUp 样板。
- 新测试优先放在对应 app 的 `tests.py`；单文件超过约 600 行时按域拆 `tests/` 包（如 `tests/test_assembly.py`、`tests/test_check.py`）。
- 每个状态变更动作至少一个正向用例 + 一个权限/约束反向用例。
- 涉及审计留痕的改动必须验证 `OperationAuditLog` / `AssetLifecycleEvent` 的只增不改约束仍然生效。

