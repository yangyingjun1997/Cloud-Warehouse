const api = require('../../utils/request')
const { statusText } = require('../../utils/format')

Page({
  data: { assetId: '', asset: null, isOperator: false, photoUrl: '', uploading: false, lifecycle: [] },

  onLoad(options) {
    this.setData({ assetId: options.id || '' })
  },

  onShow() {
    if (!this.data.assetId) return wx.navigateBack()
    const app = getApp()
    const userPromise = app.globalData.user ? Promise.resolve(app.globalData.user) : app.loadMe()
    userPromise.then((user) => {
      this.setData({ isOperator: Boolean(user && user.is_warehouse_operator) })
      this.loadAsset()
    })
  },

  onPullDownRefresh() { this.loadAsset().finally(() => wx.stopPullDownRefresh()) },

  loadAsset() {
    return api.request({ url: `/api/inventory/assets/${this.data.assetId}/` }).then((asset) => {
      this.setData({
        asset: Object.assign(asset, { statusText: statusText(asset.status) }),
        photoUrl: api.mediaUrl(asset.photo_url),
      })
      this.loadLifecycle()
    }).catch(api.showError)
  },

  loadLifecycle() {
    return api.request({ url: `/api/inventory/assets/${this.data.assetId}/lifecycle/` }).then((data) => {
      this.setData({ lifecycle: data.events || [] })
    }).catch(() => {})
  },

  createRequest(event) {
    const type = event.currentTarget.dataset.type
    wx.navigateTo({ url: `/pages/request-create/request-create?assetId=${this.data.assetId}&type=${type}` })
  },

  choosePhoto() {
    wx.chooseMedia({
      count: 1,
      mediaType: ['image'],
      sourceType: ['camera', 'album'],
      success: (result) => this.uploadPhoto(result.tempFiles[0].tempFilePath),
    })
  },

  uploadPhoto(filePath) {
    this.setData({ uploading: true })
    wx.showLoading({ title: '正在上传' })
    api.uploadFile(`/api/inventory/assets/${this.data.assetId}/upload-photo/`, filePath)
      .then((asset) => {
        wx.showToast({ title: '照片已更新', icon: 'success' })
        this.setData({ asset: Object.assign(asset, { statusText: statusText(asset.status) }), photoUrl: api.mediaUrl(asset.photo_url) })
      })
      .catch(api.showError)
      .finally(() => { this.setData({ uploading: false }); wx.hideLoading() })
  },

  previewPhoto() {
    if (this.data.photoUrl) wx.previewImage({ urls: [this.data.photoUrl] })
  },
})
