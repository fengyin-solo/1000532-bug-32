import { defineStore } from 'pinia'

import { request } from '@/api/client'

export type GroupInfo = { id: string; 名称: string; 单位: string }
export type AccountInfo = {
  id: string
  名称: string
  单位: string
  角色: 'editor' | 'viewer' | 'admin'
  只读: boolean
  项目组: GroupInfo[]
  active: boolean
}

type BootstrapResponse = { token: string; auth_epoch: number; account: AccountInfo }

const SELECTED_KEY = 'hydro.selectedAccount'

/**
 * 会话口径（修复后）：
 * - 只持久化「选中的账号 id」，绝不缓存会话票据——刷新/重进必须重新引导；
 * - bootstrap 重新取一次归属并拿到携带当前权限版本的票据；
 * - 转组/撤权后旧票据被后端注销，写请求收到 409 即清空本地会话，强制重新进入工作台。
 */
export const useSessionStore = defineStore('session', {
  state: () => ({
    accounts: [] as AccountInfo[],
    groups: [] as GroupInfo[],
    selectedId: localStorage.getItem(SELECTED_KEY) ?? '',
    account: null as AccountInfo | null,
    token: '',
    authEpoch: 0,
    /** 工作台最近一次拉取的归属版本；每次进入工作台重新引导后刷新 */
    workbenchEpoch: 0,
    staleMessage: '',
  }),
  getters: {
    isBootstrapped: (state) => Boolean(state.token && state.account),
    isReadonly: (state) => state.account?.角色 === 'viewer',
    isAdmin: (state) => state.account?.角色 === 'admin',
    canOperate: (state) => Boolean(state.token) && state.account?.角色 !== 'viewer',
  },
  actions: {
    async loadAccounts() {
      if (this.accounts.length) {
        return
      }
      const [accountsResponse, groupsResponse] = await Promise.all([
        request('/api/auth/accounts'),
        request('/api/auth/groups'),
      ])
      const accountsBody = (await accountsResponse.json()) as { items: AccountInfo[] }
      const groupsBody = (await groupsResponse.json()) as { items: GroupInfo[] }
      this.accounts = accountsBody.items
      this.groups = groupsBody.items
    },
    groupName(groupId: string | null | undefined): string {
      const id = groupId ?? ''
      return this.groups.find((group) => group.id === id)?.名称 ?? id
    },
    async bootstrap(accountId?: string) {
      const target = accountId ?? this.selectedId
      if (!target) {
        throw new Error('请先选择值班账号')
      }
      const response = await request('/api/auth/bootstrap', {
        method: 'POST',
        body: JSON.stringify({ account_id: target }),
      })
      if (!response.ok) {
        throw new Error(`会话引导失败（${response.status}）`)
      }
      const body = (await response.json()) as BootstrapResponse
      this.token = body.token
      this.authEpoch = body.auth_epoch
      this.account = body.account
      this.selectedId = body.account.id
      this.workbenchEpoch = body.auth_epoch
      this.staleMessage = ''
      localStorage.setItem(SELECTED_KEY, body.account.id)
    },
    selectAccount(accountId: string) {
      this.selectedId = accountId
      localStorage.setItem(SELECTED_KEY, accountId)
      this.token = ''
      this.account = null
    },
    /** 权限变更导致旧会话失效：清掉票据，页面引导用户重新进入工作台。 */
    invalidate(message: string) {
      this.token = ''
      this.account = null
      this.authEpoch = 0
      this.staleMessage = message
    },
  },
})
