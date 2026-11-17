const api = require('../../utils/request')

Page({
  data: { username: '', password: '', loading: false },
  onLoad() {
    if (wx.getStorageSync('token')) wx.switchTab({ url: '/pages/index/index' })
  },
  onUsernameInput(event) { this.setData({ username: event.detail.value }) },
  onPasswordInput(event) { this.setData({ password: event.detail.value }) },
  login() {
    const { username, password } = this.data
    if (!username || !password) return wx.showToast({ title: '请输入账号和密码', icon: 'none' })
    this.setData({ loading: true })
    api.request({ url: '/api/auth/token/', method: 'POST', data: { username, password } })
      .then((data) => { wx.setStorageSync('token', data.token); return getApp().loadMe() })
      .then(() => wx.switchTab({ url: '/pages/index/index' }))
      .catch(api.showError)
      .finally(() => this.setData({ loading: false }))
  },
})
