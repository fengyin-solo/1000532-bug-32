import { defineStore } from 'pinia'

import { fetchJson, postJson, setToken, clearToken } from '@/api/client'

type Operator = {
  username: string
  name: string
  unit: string
  role: string
}

type WorkbenchItem = {
  id: number
  观测编号: string
  观测类型: string
  所在钻孔: string
  静止水位?: string
  降深?: string
  出水量?: string
  观测日期?: string
  观测状态?: string
  status?: string
  所属项目组: string
  所属项目组名称: string
  所属单位: string
  归属来源: string
  can_edit: boolean
}

type LoginResponse = {
  token: string
  username: string
  unit: string
  role: string
  auth_version: number
}

type WorkbenchResponse = {
  operator: Operator
  auth_version: number
  total: number
  items: WorkbenchItem[]
}

/** 演示环境可切换的账号名册（真实环境由后端登录接口下发）。 */
export const DEMO_ACCOUNTS: { value: string; label: string; role: string }[] = [
  { value: 'admin_a', label: '一队管理员（admin_a）', role: 'admin' },
  { value: 'editor_a1', label: '一队·王观测（editor_a1）', role: 'editor' },
  { value: 'editor_a2', label: '一队·李观测（editor_a2）', role: 'editor' },
  { value: 'viewer_a', label: '一队查阅员（viewer_a · 只读）', role: 'viewer' },
  { value: 'editor_b1', label: '二队·赵观测（editor_b1）', role: 'editor' },
  { value: 'viewer_b', label: '二队查阅员（viewer_b · 只读）', role: 'viewer' },
]

export const useSessionStore = defineStore('session', {
  state: () => ({
    token: '',
    operator: null as Operator | null,
    authVersion: 0,
    workbench: [] as WorkbenchItem[],
    workbenchLoadedAt: '',
    shiftLabel: '白班 08:00-20:00',
    scope: '地质勘探数据管理平台',
  }),
  getters: {
    loggedIn: (state) => Boolean(state.token),
    isReadonly: (state) => state.operator?.role === 'viewer',
    isAdmin: (state) => state.operator?.role === 'admin',
    /** 工作台只按服务端最新返回渲染，避免使用本地残留的旧任务缓存。 */
    editablePoints: (state) => state.workbench.filter((item) => item.can_edit),
  },
  actions: {
    async login(username: string) {
      const data = await postJson<LoginResponse>('/api/auth/login', { username })
      this.token = data.token
      setToken(data.token)
      this.operator = {
        username: data.username,
        name: DEMO_ACCOUNTS.find((account) => account.value === data.username)?.label
          ?? data.username,
        unit: data.unit,
        role: data.role,
      }
      this.authVersion = data.auth_version
      // 登录成功即拉一次工作台，得到当前归属
      await this.loadWorkbench()
    },

    /** 重新进入工作台：强制重新取一次归属，并对齐授权版本（旧缓存整体覆盖）。 */
    async loadWorkbench() {
      const result = await fetchJson<WorkbenchResponse>('/api/auth/workbench')
      this.operator = result.operator
      this.authVersion = result.auth_version
      this.workbench = result.items
      this.workbenchLoadedAt = new Date().toLocaleTimeString()
      return result
    },

    logout() {
      this.token = ''
      this.operator = null
      this.authVersion = 0
      this.workbench = []
      this.workbenchLoadedAt = ''
      clearToken()
    },

    /** 页面刷新后用 sessionStorage 里的令牌恢复登录态，归属随后端重新拉。 */
    hydrate(token: string) {
      this.token = token
    },

    setShift(label: string) {
      this.shiftLabel = label
    },
  },
})
