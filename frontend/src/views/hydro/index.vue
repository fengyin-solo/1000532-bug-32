<template>
  <section class="page" data-module="hydro">
    <header class="page-head">
      <div>
        <h2>水文地质管理</h2>
        <p class="page-desc">归属以观测点当前项目组为准：只读账号无动作按钮，跨单位任务不可提交；转组后请回工作台重新进入。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" :disabled="!store.canOperate" @click="openCreate">登记水文观测点</button>
        <button class="btn" type="button" @click="exportRows">导出水文地质清单</button>
      </div>
    </header>

    <div v-if="!store.account" class="entry-hint">请先在右上角选择账号并进入工作台。</div>
    <template v-else>
      <form class="filter-bar" @submit.prevent="reload">
        <label v-for="field in filterFields" :key="field" class="filter-item">
          <span>{{ field }}</span>
          <input v-model="filters[field]" :placeholder="`按${field}检索`" />
        </label>
        <button class="btn" type="submit">查询</button>
        <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
      </form>

      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in columns" :key="column">{{ column }}</th>
            <th>当前归属</th>
            <th>转组结论</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="String(row.id)">
            <td v-for="column in columns" :key="column">
              <a v-if="column === '观测编号'" class="link" @click="openDetail(row)">{{ row[column] ?? '—' }}</a>
              <template v-else>{{ row[column] ?? '—' }}</template>
            </td>
            <td>
              <span :title="row.所属单位">{{ groupName(row.项目组) }}</span>
              <em v-if="!writable(row)" class="lock-hint">🔒</em>
            </td>
            <td>{{ row.转组结论 || '—' }}</td>
            <td class="row-actions">
              <template v-if="writable(row)">
                <button
                  v-for="action in actions"
                  :key="action"
                  class="link"
                  type="button"
                  @click="runAction(action, row)"
                >
                  {{ action }}
                </button>
                <button class="link" type="button" @click="openTransfer(row)">转组</button>
                <button class="link" type="button" @click="openDetail(row)">详情</button>
              </template>
              <template v-else>
                <button class="link" type="button" @click="openDetail(row)">详情</button>
                <span class="readonly-hint">只读/非本组</span>
              </template>
            </td>
          </tr>
          <tr v-if="!rows.length">
            <td :colspan="columns.length + 3" class="empty-state">暂无水文地质数据，或本账号单位下没有可见观测点</td>
          </tr>
        </tbody>
      </table>

      <footer class="page-foot">
        <span>共 {{ total }} 条水文地质记录（{{ store.account.单位 }} 口径）</span>
        <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      </footer>
    </template>

    <!-- 观测详情：归属、可写口径、审计基准都由服务端实时给出 -->
    <div v-if="detail" class="drawer-mask" @click.self="detail = null">
      <aside class="drawer">
        <header class="drawer-head">
          <h3>观测详情 · {{ detail.观测编号 }}</h3>
          <button class="btn ghost" type="button" @click="detail = null">关闭</button>
        </header>
        <dl class="detail-grid">
          <template v-for="column in columns" :key="column">
            <dt>{{ column }}</dt>
            <dd>{{ detail[column] ?? '—' }}</dd>
          </template>
          <dt>当前项目组</dt>
          <dd>{{ detail.项目组名称 }}（{{ detail.所属单位 }}）</dd>
          <dt>归属来源</dt>
          <dd>{{ detail.归属来源 || '—' }}</dd>
          <dt>转组结论</dt>
          <dd>{{ detail.转组结论 || '—' }}</dd>
          <dt>当前账号</dt>
          <dd>
            <span :class="detail.当前账号可写 ? 'tag writable' : 'tag readonly'">
              {{ detail.当前账号可写 ? '可改动观测数据' : '只读（不能改动）' }}
            </span>
          </dd>
        </dl>
        <h4>审计台账（历史记录按原组保留）</h4>
        <table class="data-table compact">
          <thead>
            <tr><th>动作</th><th>操作时间</th><th>审计归属组</th><th>说明</th></tr>
          </thead>
          <tbody>
            <tr v-for="(item, idx) in detail.审计记录" :key="idx">
              <td>{{ item.动作 }}</td>
              <td>{{ item.操作时间 ?? '—' }}</td>
              <td>{{ groupName(item.审计归属组) }}</td>
              <td>{{ item.说明 }}</td>
            </tr>
          </tbody>
        </table>
      </aside>
    </div>

    <!-- 转组面板：目标组限定同单位，跨单位提交服务端也会拒绝 -->
    <div v-if="transferTarget" class="drawer-mask" @click.self="transferTarget = null">
      <aside class="drawer">
        <header class="drawer-head">
          <h3>观测类型转组 · {{ transferTarget.观测编号 }}</h3>
          <button class="btn ghost" type="button" @click="transferTarget = null">关闭</button>
        </header>
        <p class="form-hint">
          当前归属：<strong>{{ groupName(transferTarget.项目组) }}</strong>（{{ transferTarget.所属单位 }}）。
          转组结论将原子回写到观测台账、工作台清单与钻孔引用页，三处口径统一。
        </p>
        <label class="filter-item">
          <span>目标项目组（同单位）</span>
          <select v-model="transferGroup">
            <option v-for="group in transferableGroups" :key="group.id" :value="group.id">
              {{ group.名称 }}
            </option>
          </select>
        </label>
        <label class="filter-item">
          <span>原因</span>
          <input v-model="transferReason" placeholder="如：观测类型调整" />
        </label>
        <div class="drawer-actions">
          <button class="btn primary" type="button" :disabled="!transferGroup" @click="submitTransfer">确认转组</button>
          <span v-if="transferError" class="error-text">{{ transferError }}</span>
        </div>
      </aside>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { ForbiddenError, request, SessionStaleError } from '@/api/client'
import type { GroupInfo } from '@/stores/session'
import { useSessionStore } from '@/stores/session'

type Row = Record<string, string | number | null> & {
  项目组?: string
  所属单位?: string
  转组结论?: string
  归属来源?: string
}

type Detail = Row & {
  项目组名称: string
  当前账号可写: boolean
  审计记录: { 动作: string; 操作时间: number | null; 审计归属组: string; 说明: string }[]
}

const ENDPOINT = '/api/hydro'
const columns = ['观测编号', '观测类型', '所在钻孔', '静止水位', '降深', '出水量', '观测日期', '观测状态']
const actions = ['记录观测', '标记异常', '复核数据']

const store = useSessionStore()
const route = useRoute()

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

const detail = ref<Detail | null>(null)
const transferTarget = ref<Row | null>(null)
const transferGroup = ref('')
const transferReason = ref('观测类型转组')
const transferError = ref('')

/** 同单位项目组才出现在转组候选里——跨单位提交一律拒绝（前端先收敛，后端再兜底）。 */
const transferableGroups = computed<GroupInfo[]>(() => {
  if (!store.account || !transferTarget.value) {
    return []
  }
  if (store.isAdmin) {
    const unit = String(transferTarget.value.所属单位 ?? '')
    return store.accounts
      .flatMap((account) => account.项目组)
      .filter((group, index, all) => all.findIndex((item) => item.id === group.id) === index)
      .filter((group) => group.单位 === unit && group.id !== transferTarget.value?.项目组)
  }
  return store.account.项目组.filter((group) => group.id !== transferTarget.value?.项目组)
})

function groupName(groupId: unknown): string {
  return store.groupName(String(groupId ?? ''))
}

/** 可写口径完全服务端：管理员可写；经办人仅当点在自己组。列表按单位过滤，故不会出现跨单位行。 */
function writable(row: Row): boolean {
  if (!store.account) {
    return false
  }
  if (store.account.角色 === 'viewer') {
    return false
  }
  if (store.account.角色 === 'admin') {
    return true
  }
  return store.account.项目组.some((group) => group.id === row.项目组)
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export/all`, '_blank')
}

function openCreate() {
  errorMessage.value = '水文观测点登记请走审批流入口（归属将按观测类型当前项目组确定）'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    if (!response.ok) {
      throw new Error('水文地质动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    reportError(error, '水文地质操作失败')
  }
}

function openTransfer(row: Row) {
  transferTarget.value = row
  transferGroup.value = ''
  transferError.value = ''
  transferReason.value = '观测类型转组'
}

async function submitTransfer() {
  if (!transferTarget.value) {
    return
  }
  transferError.value = ''
  try {
    const response = await request(`${ENDPOINT}/${transferTarget.value.id}/transfer`, {
      method: 'POST',
      body: JSON.stringify({ target_group: transferGroup.value, reason: transferReason.value }),
    })
    if (!response.ok) {
      throw new Error('转组未生效')
    }
    transferTarget.value = null
    // 转组已抬升权限版本：本地票据作废，引导用户重新进入工作台，杜绝旧任务继续提交
    store.invalidate('转组已生效，权限版本已更新')
    errorMessage.value = '转组成功，三处台账已同步；请重新进入工作台后继续操作。'
    await reload().catch(() => undefined)
  } catch (error) {
    reportError(error, '转组失败')
  }
}

async function openDetail(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}`)
    detail.value = (await response.json()) as Detail
  } catch (error) {
    reportError(error, '观测详情读取失败')
  }
}

function reportError(error: unknown, fallback: string) {
  if (error instanceof SessionStaleError) {
    errorMessage.value = `${error.message}（旧任务不可继续提交，请重新进入工作台）`
  } else if (error instanceof ForbiddenError) {
    errorMessage.value = error.message
  } else {
    errorMessage.value = error instanceof Error ? error.message : fallback
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('水文观测点列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    const focus = route.query.focus
    if (focus) {
      const target = rows.value.find((row) => String(row.id) === String(focus))
      if (target) {
        await openDetail(target)
      }
    }
  } catch (error) {
    reportError(error, '水文地质列表读取失败')
  }
}

onMounted(reload)
</script>

<style scoped>
.entry-hint {
  padding: 20px;
  text-align: center;
  color: #7a7a7a;
  border: 1px dashed #d0d0d0;
  border-radius: 8px;
}
.lock-hint {
  margin-left: 4px;
  font-style: normal;
  font-size: 12px;
}
.readonly-hint {
  color: #999;
  font-size: 12px;
}
.drawer-mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.35);
  display: flex;
  justify-content: flex-end;
  z-index: 50;
}
.drawer {
  width: 640px;
  max-width: 92vw;
  height: 100%;
  background: #fff;
  padding: 20px 24px;
  overflow-y: auto;
  box-shadow: -4px 0 16px rgba(0, 0, 0, 0.12);
}
.drawer-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.detail-grid {
  display: grid;
  grid-template-columns: 110px 1fr;
  gap: 6px 12px;
  font-size: 13px;
}
.detail-grid dt {
  color: #777;
}
.detail-grid dd {
  margin: 0;
}
.compact {
  font-size: 12px;
}
.form-hint {
  font-size: 13px;
  color: #555;
  margin-bottom: 12px;
}
.drawer-actions {
  margin-top: 14px;
  display: flex;
  align-items: center;
  gap: 12px;
}
.tag {
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 12px;
}
.tag.writable {
  background: #f6ffed;
  color: #389e0d;
  border: 1px solid #b7eb8f;
}
.tag.readonly {
  background: #fafafa;
  color: #8c8c8c;
  border: 1px solid #d9d9d9;
}
</style>
