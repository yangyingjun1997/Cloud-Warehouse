const { API_BASE_URL } = require('./config')

function errorMessage(data, fallback) {
  if (!data) return fallback
  if (typeof data === 'string') return data
  if (data.detail) return Array.isArray(data.detail) ? data.detail.join('；') : data.detail
  const firstKey = Object.keys(data)[0]
  if (!firstKey) return fallback
  const value = data[firstKey]
  return `${firstKey}：${Array.isArray(value) ? value.join('；') : String(value)}`
}

function authHeader(header = {}) {
  const token = wx.getStorageSync('token')
  return Object.assign({}, header, token ? { Authorization: `Token ${token}` } : {})
}

function handleUnauthorized(statusCode) {
  if (statusCode !== 401) return
  wx.removeStorageSync('token')
  wx.reLaunch({ url: '/pages/login/login' })
}

function request(options = {}, attempt = 0) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${API_BASE_URL}${options.url}`,
      method: options.method || 'GET',
      data: options.data || {},
      header: authHeader(options.header),
      timeout: options.timeout || 10000,
      success(response) {
        if (response.statusCode >= 200 && response.statusCode < 300) {
          resolve(response.data)
          return
        }
        const retryCount = options.retryCount === undefined ? 1 : options.retryCount
        if (response.statusCode === 503 && attempt < retryCount) {
          setTimeout(() => request(options, attempt + 1).then(resolve).catch(reject), 500 * (attempt + 1))
          return
        }
        handleUnauthorized(response.statusCode)
        reject(new Error(errorMessage(response.data, `请求失败（${response.statusCode}）`)))
      },
      fail(error) { reject(new Error(error.errMsg || '网络请求失败')) },
    })
  })
}

function uploadFile(url, filePath, name = 'photo') {
  return new Promise((resolve, reject) => {
    wx.uploadFile({
      url: `${API_BASE_URL}${url}`,
      filePath,
      name,
      header: authHeader(),
      success(response) {
        let data = {}
        try { data = JSON.parse(response.data || '{}') } catch (error) {
          reject(new Error('服务器返回了无法识别的数据'))
          return
        }
        if (response.statusCode >= 200 && response.statusCode < 300) {
          resolve(data)
          return
        }
        handleUnauthorized(response.statusCode)
        reject(new Error(errorMessage(data, `上传失败（${response.statusCode}）`)))
      },
      fail(error) { reject(new Error(error.errMsg || '照片上传失败')) },
    })
  })
}

function mediaUrl(path) {
  if (!path) return ''
  return /^https?:\/\//.test(path) ? path : `${API_BASE_URL}${path}`
}

function showError(error) {
  wx.showToast({ title: error.message || '网络请求失败', icon: 'none', duration: 2400 })
}

module.exports = { request, uploadFile, mediaUrl, showError }
