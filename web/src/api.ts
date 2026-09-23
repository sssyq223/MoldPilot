function networkError(error: unknown) {
  const message = error instanceof Error ? error.message : String(error || '')
  if (/failed to fetch|networkerror|load failed/i.test(message)) {
    return Object.assign(new Error('无法连接工作台服务。请使用 http://127.0.0.1:5173 打开页面，并确认后端已启动。'), {status: 0})
  }
  return error instanceof Error ? error : new Error(message || '无法连接工作台服务')
}

export async function api<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const cookies=document.cookie.split('; ')
  const csrf=cookies.find(c=>c.startsWith('agent_csrf='))?.split('=')[1]??''
  let response: Response
  try {
    response = await fetch(`/api${path}`, { credentials: 'same-origin', cache: 'no-store', ...options, headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf, ...options.headers } })
  } catch (error) {
    throw networkError(error)
  }
  const raw=await response.text()
  let body:any={}
  try{body=raw?JSON.parse(raw):{}}catch{}
  if (!response.ok) {
    const message=body.error?.message ?? (typeof body.detail==='string' ? (body.detail==='Not Found'?'接口不存在，请检查后端服务是否已更新':body.detail) : body.detail?.[0]?.msg) ?? `服务返回异常（${response.status}），请稍后重试`
    throw Object.assign(new Error(message), {status:response.status,code:body.error?.code})
  }
  return body
}
export const post = (path: string, body: unknown = {}) => api(path, { method: 'POST', body: JSON.stringify(body) })
export function shanghai(value: string | null | undefined) {
  if (!value) return '时间待确认'
  const date=new Date(value)
  if(!Number.isFinite(date.getTime()))return '时间待确认'
  return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'short', timeStyle: 'short' }).format(date)
}
