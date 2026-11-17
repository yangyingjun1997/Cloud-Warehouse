const api = require('../../utils/request')
const { statusText } = require('../../utils/format')

Page({
  data: { user: {}, avatarText: '用', query: '', assets: [], searching: false, requestCount: 0, unreadCount: 0 },

  onShow() {
    const app = getApp()
    const userPromise = app.globalData.user ? Promise.resolve(app.globalData.user) : app.loadMe()
    userPromise.then((user) => {
      user = user || {}
      this.setData({ user, avatarText: (user.full_name || user.username || '用').slice(0, 1) })
      this.loadSummary()
    })
  },

  loadSummary() {
    Promise.all([
      api.request({ url: '/api/workflow/requests/' }),
      api.request({ url: '/api/notifications/notifications/unread-count/' }),
    ]).then(([requests, notifications]) => {
      const rows = requests.results || requests
      const user = getApp().globalData.user || {}
      this.setData({
        requestCount: rows.filter((item) => String(item.applicant) === String(user.id)).length,
        unreadCount: notifications.count || 0,
      })
    }).catch(api.showError)
  },

  onSearchInput(event) { this.setData({ query: event.detail.value }) },
  searchAssets() {
    const query = this.data.query.trim()
    if (!query) return wx.showToast({ title: '请输入资产名称、编码或序列号', icon: 'none' })
    this.setData({ searching: true })
    api.request({ url: `/api/inventory/assets/search/?q=${encodeURIComponent(query)}` })
      .then((data) => this.setData({ assets: (data.results || []).map((asset) => Object.assign(asset, { statusText: statusText(asset.status) })) }))
      .catch(api.showError)
      .finally(() => this.setData({ searching: false }))
  },

  scanAsset() {
    wx.scanCode({
      onlyFromCamera: false,
      scanType: ['qrCode', 'barCode'],
      success: (result) => {
        wx.showLoading({ title: '正在查询' })
        api.request({ url: `/api/inventory/assets/lookup/?code=${encodeURIComponent(result.result)}` })
          .then((asset) => this.goAsset(asset.id))
          .catch(api.showError)
          .finally(() => wx.hideLoading())
      },
    })
  },

  goAsset(assetId) { wx.navigateTo({ url: `/pages/asset-detail/asset-detail?id=${assetId}` }) },
  goRequests() { wx.switchTab({ url: '/pages/requests/requests' }) },
  goNotifications() { wx.switchTab({ url: '/pages/notifications/notifications' }) },
})
