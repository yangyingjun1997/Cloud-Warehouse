const STATUS_TEXT = {
  draft: '草稿', pending: '待审批', rejected: '已驳回', approved: '已通过',
  waiting_warehouse: '待仓库处理', done: '已完成', canceled: '已撤回', closed: '异常关闭',
  in_stock: '在库', borrowed: '已借出', issued: '已领用', pending_out: '待出库',
  pending_return: '待归还验收', pending_inspection: '待质检', maintenance: '待维修',
  repairing: '维修中', sold: '已售出', returned_to_vendor: '已退货', scrapped: '已报废',
  damaged: '已报损', lost: '已丢失', disabled: '已停用',
}

const REQUEST_TYPE_TEXT = {
  borrow: '借用', return: '归还', issue: '领用', transfer: '调拨', repair: '返修',
  damage: '报损', sale: '售出', return_to_vendor: '退货', scrap: '报废',
}

function statusText(value) { return STATUS_TEXT[value] || value || '-' }
function requestTypeText(value) { return REQUEST_TYPE_TEXT[value] || value || '-' }
function formatDate(value) { return value ? String(value).replace('T', ' ').slice(0, 16) : '-' }

module.exports = { statusText, requestTypeText, formatDate }
