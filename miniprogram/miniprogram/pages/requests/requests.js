const api = require('../../utils/request')
const { statusText, requestTypeText, formatDate } = require('../../utils/format')

function decorateRequest(item, user, isOperator) {
  const statusClass = ['rejected', 'canceled'].includes(item.status)
    ? 'status-rejected'
    : item.status === 'done' ? 'status-done' : item.status === 'pending' ? 'status-pending' : ''
  const names = (item.lines || []).map((line) => line.asset_name || line.asset_code || line.stock_item_name).filter(Boolean)
  return Object.assign(item, {
    statusText: statusText(item.status),
    requestTypeText: requestTypeText(item.request_type),
    createdAtText: formatDate(item.created_at),
    assetSummary: names.join('、') || '未填写物品',
    statusClass,
    isMine: String(item.applicant) === String(user.id),
    isOperatorTask: isOperator && String(item.applicant) !== String(user.id) && ['pending', 'waiting_warehouse'].includes(item.status),
  })
}

Page({
  data: { myRequests: [], tasks: [], selectedSection: 'mine', isOperator: false },

  onShow() {
    const app = getApp()
    const userPromise = app.globalData.user ? Promise.resolve(app.globalData.user) : app.loadMe()
    userPromise.then(() => this.loadRequests())
  },
  onPullDownRefresh() { this.loadRequests().finally(() => wx.stopPullDownRefresh()) },

  loadRequests() {
    return api.request({ url: '/api/workflow/requests/' }).then((data) => {
      const user = getApp().globalData.user || {}
      const isOperator = Boolean(user.is_warehouse_operator)
      const rows = (data.results || data).map((item) => decorateRequest(item, user, isOperator))
      this.setData({
        myRequests: rows.filter((item) => item.isMine),
        tasks: rows.filter((item) => item.isOperatorTask),
        isOperator,
      })
    }).catch(api.showError)
  },

  selectSection(event) { this.setData({ selectedSection: event.currentTarget.dataset.section }) },
  createRequest() { wx.navigateTo({ url: '/pages/request-create/request-create' }) },
  openRequest(event) { wx.navigateTo({ url: `/pages/request-detail/request-detail?id=${event.currentTarget.dataset.id}` }) },
})
