/** 统一请求封装：拼后端地址、注入会话票据、按状态码驱动重新引导与失效提示。 */
import { useSessionStore } from '@/stores/session'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

/** 409/401：会话已失效（转组/撤权后旧票据）或未建立会话，前端必须重新进入工作台。 */
export class SessionStaleError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'SessionStaleError'
  }
}

/** 403：授权被拒（只读、跨单位、已撤权）。 */
export class ForbiddenError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ForbiddenError'
  }
}

export function request(path: string, init?: RequestInit): Promise<Response> {
  const url = path.startsWith('http') ? path : `${API_BASE}${path}`
  const session = useSessionStore()
  const headers = new Headers(init?.headers ?? { 'Content-Type': 'application/json' })
  if (session.token && !headers.has('X-Session-Token')) {
    headers.set('X-Session-Token', session.token)
  }
  return fetch(url, { ...init, headers })
    .then(async (response) => {
      if (response.status === 401 || response.status === 409) {
        const detail = await readDetail(response)
        session.invalidate(detail)
        throw new SessionStaleError(detail)
      }
      if (response.status === 403) {
        throw new ForbiddenError(await readDetail(response))
      }
      return response
    })
    .catch((error: unknown) => {
      if (error instanceof SessionStaleError || error instanceof ForbiddenError) {
        throw error
      }
      const detail = error instanceof Error ? error.message : '请求未送达'
      throw new Error(`接口请求失败：${detail}`)
    })
}

async function readDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: string }
    return body.detail ?? `接口返回 ${response.status}`
  } catch {
    return `接口返回 ${response.status}，请重新进入工作台`
  }
}

export async function fetchJson<T>(path: string): Promise<T> {
  const response = await request(path)
  if (!response.ok) {
    throw new Error(`接口返回 ${response.status}，数据未更新`)
  }
  return (await response.json()) as T
}
