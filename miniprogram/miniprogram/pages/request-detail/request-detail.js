const api = require('../../utils/request')
const { statusText, requestTypeText, formatDate } = require('../../utils/format')

Page({
  data: { id: '', request: null, isOperator: false, canApprove: false, canProcess: false, canWithdraw: false, canHide: false, rejecting: false, rejectComment: '', processDate: '' },

  onLoad(options) { this.setData({ id: options.id || '' }) },
  onShow() {
    if (!this.data.id) return wx.navigateBack()
    const app = getApp()
    const userPromise = app.globalData.user ? Promise.resolve(app.globalData.user) : app.loadMe()
    userPromise.then((user) => { this.setData({ isOperator: Boolean(user && user.is_warehouse_operator) }); this.loadRequest() })
  },
  onPullDownRefresh() { this.loadRequest().finally(() => wx.stopPullDownRefresh()) },

  loadRequest() {
    return api.request({ url: `/api/workflow/requests/${this.data.id}/` }).then((request) => {
      const user = getApp().globalData.user || {}
      const task = (request.approval_tasks || []).find((item) => item.status === 'pending')
      const isMine = String(request.applicant) === String(user.id)
      const isOperator = Boolean(user.is_warehouse_operator)
      const lines = (request.lines || []).map((line) => Object.assign(line, { statusText: statusText(line.asset_status) }))
      const logs = (request.approval_logs || []).map((log) => Object.assign(log, { createdAtText: formatDate(log.created_at) }))
      this.setData({
        processDate: request.transaction_date || new Date().toISOString().slice(0, 10),
        request: Object.assign(request, {
          lines,
          approval_logs: logs,
          statusText: statusText(request.status),
          requestTypeText: requestTypeText(request.request_type),
          createdAtText: formatDate(request.created_at),
          updatedAtText: formatDate(request.updated_at),
        }),
        canApprove: isOperator && request.status === 'pending' && (!task || !task.assigned_to || String(task.assigned_to) === String(user.id)),
        canProcess: isOperator && request.status === 'waiting_warehouse',
        canWithdraw: isMine && request.status === 'pending',
        canHide: isMine && ['rejected', 'canceled'].includes(request.status),
        rejecting: false,
        rejectComment: '',
      })
    }).catch(api.showError)
  },

  openAsset(event) {
    const assetId = event.currentTarget.dataset.id
    if (assetId) wx.navigateTo({ url: `/pages/asset-detail/asset-detail?id=${assetId}` })
  },
  approve() { this.confirmAction('审核通过', '确认将此申请转交仓库执行？', 'approve') },
  process() { this.confirmAction('完成出入库', '确认已完成实物核对和出入库操作？', 'process') },
  onProcessDateChange(event) { this.setData({ processDate: event.detail.value }) },
  withdraw() { this.confirmAction('撤回申请', '撤回后该申请不会再进入审批流程。', 'withdraw') },
  hide() { this.confirmAction('删除个人记录', '此操作仅会从您的列表中隐藏该记录。', 'hide') },
  confirmAction(title, content, action) {
    wx.showModal({ title, content, success: (result) => { if (result.confirm) this.performAction(action) } })
  },
  startReject() { this.setData({ rejecting: true, rejectComment: '' }) },
  cancelReject() { this.setData({ rejecting: false, rejectComment: '' }) },
  onRejectInput(event) { this.setData({ rejectComment: event.detail.value }) },
  confirmReject() {
    if (!this.data.rejectComment.trim()) return wx.showToast({ title: '请填写驳回原因', icon: 'none' })
    this.performAction('reject', this.data.rejectComment)
  },
  performAction(action, comment = '') {
    wx.showLoading({ title: '正在处理' })
    const data = { comment }
    if (action === 'process' && this.data.processDate) data.transaction_date = this.data.processDate
    api.request({ url: `/api/workflow/requests/${this.data.id}/${action}/`, method: 'POST', data })
      .then(() => { wx.showToast({ title: '操作成功', icon: 'success' }); return this.loadRequest() })
      .catch(api.showError)
      .finally(() => wx.hideLoading())
  },
})
