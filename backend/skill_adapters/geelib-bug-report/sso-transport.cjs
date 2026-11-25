// Transport for the unchanged private report-bug.mjs. The reporting user's token
// enters via stdin only. The skill owns field resolution, formatting and creation.
const fs = require('node:fs')
const http = require('node:http')
const https = require('node:https')
const path = require('node:path')
const context = JSON.parse(fs.readFileSync(0, 'utf8'))
const base = new URL(context.apiUrl)
const fields = '/openapi/Matter/getMatterTypeCustomAttributeList'
const create = '/openapi/Matter/add'

function record(state, detail = '') {
  const file = path.join(process.cwd(), 'delivery.json')
  fs.writeFileSync(file + '.tmp', JSON.stringify({ state, detail }))
  fs.renameSync(file + '.tmp', file)
}

function post(url, body) {
  const transport = url.protocol === 'https:' ? https : http
  return new Promise((resolve, reject) => {
    const req = transport.request(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Agent-Auth': 'Bearer ' + context.appToken },
    }, res => {
      let data = ''
      res.setEncoding('utf8')
      res.on('data', part => { data += part })
      res.on('error', reject)
      res.on('end', () => {
        try { resolve({ status: res.statusCode, body: JSON.parse(data) }) }
        catch { reject(new Error('极库云响应不是 JSON')) }
      })
    })
    req.on('error', reject)
    req.setTimeout(12000, () => req.destroy(new Error('请求超时')))
    req.end(JSON.stringify(body))
  })
}

global.fetch = async function skillFetch(address, options) {
  const url = new URL(address)
  if (url.origin !== base.origin || ![fields, create].includes(url.pathname))
    throw new Error('技能请求与当前项目的极库云配置不符')
  const { sign, user_mail, t, ...body } = JSON.parse(options.body)
  const creating = url.pathname === create
  if (creating) {
    body.mkd_content = context.description
    record('sending')
  }
  let response
  try {
    response = await post(url, body)
  } catch (error) {
    if (creating && ['ECONNREFUSED', 'ENOTFOUND', 'EAI_AGAIN'].includes(error.code))
      record('rejected', '未连接到极库云，请稍后重试')
    throw error
  }
  const { status, body: result } = response
  if (!result || status >= 500) throw new Error('未收到有效的极库云回执')
  if (result.errno !== 2000 || status < 200 || status >= 300) {
    const message = String(result.errmsg || '极库云请求失败').slice(0, 200)
    if (creating && Number.isInteger(result.errno) && result.errno !== 2000) record('rejected', message)
    throw new Error(message)
  }
  if (creating) {
    const data = result.data
    const id = String(data?.id ?? data?.matter_id ?? data?.Id ?? data)
    if (!/^[1-9][0-9]*$/.test(id)) throw new Error('缺少有效缺陷编号')
    record('created', id)
    result.data = id
  } else {
    // QALab supplies a verified email. Expose an exact email alias to the
    // upstream name resolver only when that member is in the live project list.
    const field = result.data?.map(row => row.matterCustomAttribute).find(row => row?.cf_key === 'executor')
    if (field) {
      const members = JSON.parse(field.type_options || '[]')
      const match = members.find(m => String(m.mail || '').toLowerCase() === context.executor.toLowerCase())
      if (!match) throw new Error('当前执行人邮箱不在该极库云项目的成员列表中')
      field.type_options = JSON.stringify([{ text: context.executor, mail: match.mail }])
    }
  }
  return { status, text: async () => JSON.stringify(result) }
}
