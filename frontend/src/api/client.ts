/** 统一请求封装：拼后端地址、带会话令牌、把授权错误翻译成可识别的错误码。 */
const API_BASE = import.meta.env.VITE_API_BASE ?? ''

export type ApiError = Error & { code?: string; status?: number }

function readToken(): string {
  return sessionStorage.getItem('auth_token') ?? ''
}

export function setToken(token: string) {
  sessionStorage.setItem('auth_token', token)
}

export function clearToken() {
  sessionStorage.removeItem('auth_token')
}

export function getToken(): string {
  return readToken()
}

async function parseError(response: Response): Promise<ApiError> {
  let code = `HTTP_${response.status}`
  let message = `接口返回 ${response.status}，数据未更新`
  try {
    const payload = (await response.json()) as {
      detail?: string | { code?: string; message?: string }
    }
    if (payload.detail && typeof payload.detail === 'object') {
      code = payload.detail.code ?? code
      message = payload.detail.message ?? message
    } else if (typeof payload.detail === 'string') {
      message = payload.detail
    }
  } catch {
    /* 非 JSON 错误体时沿用兜底文案 */
  }
  const error = new Error(message) as ApiError
  error.code = code
  error.status = response.status
  return error
}

export function request(path: string, init?: RequestInit): Promise<Response> {
  const url = path.startsWith('http') ? path : `${API_BASE}${path}`
  const headers = new Headers(init?.headers)
  if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  const token = readToken()
  if (token) headers.set('X-Auth-Token', token)
  return fetch(url, { ...init, headers }).catch((error: unknown) => {
    const detail = error instanceof Error ? error.message : '请求未送达'
    throw new Error(`接口请求失败：${detail}`)
  })
}

/** 发请求并在非 2xx 时抛出带 code 的错误（permission_stale / readonly / not_found …）。 */
export async function sendJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await request(path, init)
  if (!response.ok) throw await parseError(response)
  return (await response.json()) as T
}

export async function fetchJson<T>(path: string): Promise<T> {
  return sendJson<T>(path)
}

export async function postJson<T>(path: string, body: unknown): Promise<T> {
  return sendJson<T>(path, {
    method: 'POST',
    body: JSON.stringify(body ?? {}),
  })
}
