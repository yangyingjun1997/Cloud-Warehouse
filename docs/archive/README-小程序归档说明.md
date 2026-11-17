# 小程序已暂停（2026-11-17 归档）

## 结论

微信小程序客户端**不再作为当前交付物**。本目录仅作历史留存，不参与构建、测试和发布。

## 决策依据

1. **六大需求在 Web/PWA 端已全部实现并通过现场验证**：

   | 需求 | Web 端位置 |
   | --- | --- |
   | 快速出入库 | `apps/common/views.py::quick_inventory_process`（模板 32 KB，功能最完整） |
   | 扫码连续处理 | HID 键盘模拟 + 摄像头双通道，中文输入法已现场验收 |
   | 待审批与待执行任务 | `workflow` 审批队列 + 待执行列表 |
   | 低库存和异常提醒 | `views.py::low_stock_alerts` + 通知中心 |
   | 盘点扫码 | `inventory_check_detail`（start/scan/submit/correct/review 全流程） |
   | 维修状态更新 | `asset_lifecycle` |

2. **双份维护成本高**：任一业务规则变更（自批禁止、接收人选人、人员显示等）都要在小程序里再实现一遍，已多次出现两端不同步。

3. **小程序离线能力并不优于 PWA**：两者都需要网络；PWA 已有 IndexedDB 离线队列（`offline.js`），小程序反而没有。

4. **真机运行时校验未完成**：域名虽已登记为合法域名，但小程序运行时是否放行未实测（iPhone 场景下 PWA 已可直接使用）。

## 用户决策（2026-11-17）

- 仓管员以 **iPhone 为主**
- **离线能力暂不作为本期需求**
- 因此 PWA 的 iOS 限制（无后台推送、存储配额较小）可接受
- `miniprogram/` 移入本归档目录

## 如果将来要恢复

- 小程序代码在 `docs/archive/miniprogram-paused-20261117/miniprogram/`，共 33 个文件
- `utils/config.js` 中 `API_BASE_URL` 已对准 `https://cloudwarehouse.dpdns.org`
- `README.md` 记录当时的验收顺序和边界
- 恢复前需重新评估：小程序是否仍是最优移动端形态，以及 PWA 是否已满足现场需求
