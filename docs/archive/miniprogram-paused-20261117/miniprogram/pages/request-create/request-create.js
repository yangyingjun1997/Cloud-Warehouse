const api = require('../../utils/request')
const { statusText } = require('../../utils/format')

const TYPE_OPTIONS = [
  { label: '借用', value: 'borrow' },
  { label: '领用', value: 'issue' },
  { label: '归还', value: 'return' },
]

Page({
  data: {
    typeOptions: TYPE_OPTIONS,
    typeIndex: 0,
    typeValue: 'borrow',
    selectedAsset: null,
    query: '',
    assets: [],
    visibleAssets: [],
    availableOnly: true,
    reason: '',
    recipientName: '',
    recipientCompany: '',
    recipientPhone: '',
    usageLocation: '',
    expectedReturnDate: '',
    loading: false,
  },

  onLoad(options) {
    const type = options.type || 'borrow'
    const typeIndex = Math.max(0, TYPE_OPTIONS.findIndex((item) => item.value === type))
    this.setData({ typeIndex, typeValue: TYPE_OPTIONS[typeIndex].value })
    const app = getApp()
    const userPromise = app.globalData.user ? Promise.resolve(app.globalData.user) : app.loadMe()
    userPromise.then((user) => this.setData({ recipientName: (user && (user.full_name || user.username)) || '' }))
    if (options.assetId) this.loadAsset(options.assetId)
  },

  loadAsset(id) {
    wx.showLoading({ title: '正在读取资产' })
    api.request({ url: `/api/inventory/assets/${id}/` })
      .then((asset) => this.setData({ selectedAsset: this.decorateAsset(asset) }))
      .catch(api.showError)
      .finally(() => wx.hideLoading())
  },

  decorateAsset(asset) {
    const available = this.data.typeValue === 'return' ? asset.can_return : asset.can_request
    return Object.assign(asset, { statusText: statusText(asset.status), available })
  },

  onTypeChange(event) {
    const typeIndex = Number(event.detail.value)
    this.setData({ typeIndex, typeValue: TYPE_OPTIONS[typeIndex].value }, () => {
      const selectedAsset = this.data.selectedAsset
      const assets = this.data.assets.map((asset) => this.decorateAsset(asset))
      this.setData({
        selectedAsset: selectedAsset ? this.decorateAsset(selectedAsset) : null,
        assets,
        visibleAssets: this.filterAssets(assets),
      })
    })
  },
  onFieldInput(event) { this.setData({ [event.currentTarget.dataset.field]: event.detail.value }) },
  onDateChange(event) { this.setData({ expectedReturnDate: event.detail.value }) },
  onSearchInput(event) { this.setData({ query: event.detail.value }) },
  filterAssets(assets) {
    return assets.filter((asset) => !this.data.availableOnly || asset.available)
  },
  toggleAvailable() {
    this.setData({ availableOnly: !this.data.availableOnly }, () => {
      this.setData({ visibleAssets: this.filterAssets(this.data.assets) })
    })
  },

  searchAssets() {
    const query = this.data.query.trim()
    if (!query) return wx.showToast({ title: '请输入资产名称、编码或序列号', icon: 'none' })
    wx.showLoading({ title: '正在检索' })
    api.request({ url: `/api/inventory/assets/search/?q=${encodeURIComponent(query)}` })
      .then((data) => {
        const assets = (data.results || []).map((asset) => this.decorateAsset(asset))
        this.setData({ assets, visibleAssets: this.filterAssets(assets) })
      })
      .catch(api.showError)
      .finally(() => wx.hideLoading())
  },

  scanAsset() {
    wx.scanCode({
      onlyFromCamera: false,
      scanType: ['qrCode', 'barCode'],
      success: (result) => {
        api.request({ url: `/api/inventory/assets/lookup/?code=${encodeURIComponent(result.result)}` })
          .then((asset) => this.setData({ selectedAsset: this.decorateAsset(asset) }))
          .catch(api.showError)
      },
    })
  },

  selectAsset(event) {
    const asset = this.data.assets.find((item) => String(item.id) === String(event.currentTarget.dataset.id))
    if (!asset) return
    if (!asset.available) return wx.showToast({ title: '该资产当前不可用于此申请', icon: 'none' })
    this.setData({ selectedAsset: asset })
  },

  submit() {
    const data = this.data
    const outbound = ['borrow', 'issue'].includes(data.typeValue)
    if (!data.selectedAsset) return wx.showToast({ title: '请选择一项资产', icon: 'none' })
    if (!data.selectedAsset.available) return wx.showToast({ title: '该资产当前不可用于此申请', icon: 'none' })
    if (!data.reason.trim()) return wx.showToast({ title: '请填写申请说明', icon: 'none' })
    if (outbound && !data.usageLocation.trim()) return wx.showToast({ title: '请填写使用地点', icon: 'none' })
    this.setData({ loading: true })
    api.request({
      url: '/api/workflow/requests/',
      method: 'POST',
      data: {
        request_type: data.typeValue,
        reason: data.reason.trim(),
        recipient_name: data.recipientName.trim(),
        recipient_company: data.recipientCompany.trim(),
        recipient_phone: data.recipientPhone.trim(),
        usage_location: data.usageLocation.trim(),
        expected_return_date: data.expectedReturnDate || null,
        lines: [{ asset: data.selectedAsset.id, quantity: 1 }],
      },
    }).then((request) => api.request({ url: `/api/workflow/requests/${request.id}/submit/`, method: 'POST' }))
      .then((request) => {
        wx.showToast({ title: '申请已提交', icon: 'success' })
        setTimeout(() => wx.redirectTo({ url: `/pages/request-detail/request-detail?id=${request.id}` }), 600)
      })
      .catch(api.showError)
      .finally(() => this.setData({ loading: false }))
  },
})
