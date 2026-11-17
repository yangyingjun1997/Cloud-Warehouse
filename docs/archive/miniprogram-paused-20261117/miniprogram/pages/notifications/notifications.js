const api = require('../../utils/request')
const { formatDate } = require('../../utils/format')

Page({
  data: { notifications: [], unreadCount: 0 },
  onShow() { this.loadNotifications() },
  onPullDownRefresh() { this.loadNotifications().finally(() => wx.stopPullDownRefresh()) },
  loadNotifications() {
    return api.request({ url: '/api/notifications/notifications/' }).then((data) => {
      const notifications = (data.results || data).map((item) => Object.assign(item, { createdAtText: formatDate(item.created_at) }))
      this.setData({ notifications, unreadCount: notifications.filter((item) => !item.is_read).length })
    }).catch(api.showError)
  },
  openNotification(event) {
    const id = event.currentTarget.dataset.id
    const item = this.data.notifications.find((notification) => String(notification.id) === String(id))
    if (!item) return
    const goToTarget = () => {
      if (item.related_model === 'WorkflowRequest' && item.related_object_id) {
        wx.navigateTo({ url: `/pages/request-detail/request-detail?id=${item.related_object_id}` })
      }
    }
    if (item.is_read) return goToTarget()
    api.request({ url: `/api/notifications/notifications/${id}/mark-read/`, method: 'POST' })
      .then(() => { this.loadNotifications(); goToTarget() })
      .catch(api.showError)
  },
})
