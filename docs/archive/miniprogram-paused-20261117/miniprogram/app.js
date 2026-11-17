const api = require('./utils/request')

App({
  globalData: {
    user: null,
  },

  onLaunch() {
    if (wx.getStorageSync('token')) {
      this.loadMe()
    }
  },

  loadMe() {
    return api.request({
      url: '/api/accounts/me/',
      method: 'GET',
    }).then((user) => {
      this.globalData.user = user
      return user
    }).catch(() => null)
  },
})
