<template>
  <section class="page" data-module="hydro">
    <header class="page-head">
      <div>
        <h2>水文地质管理</h2>
        <p class="page-desc">
          归属以当前项目组为准；只读账号不能改动观测数据，跨单位提交一律拒绝。
          权限更新后缓存的旧任务无法提交，请回到工作台重新取归属。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="reload">刷新台账</button>
        <button v-if="session.isAdmin" class="btn primary" type="button" @click="showTransfer = !showTransfer">
          观测类型转组
        </button>
      </div>
    </header>

    <form v-if="session.isAdmin && showTransfer" class="transfer-bar" @submit.prevent="doTransfer">
      <label class="filter-item">
        <span>观测类型</span>
        <input v-model="transferForm.obs_type" placeholder="如：稳定流抽水试验" />
      </label>
      <label class="filter-item">
        <span>目标项目组</span>
        <select v-model="transferForm.to_group">
          <option value="G-A1">第一水文组（地质一大队）</option>
          <option value="G-A2">第二水文组（地质一大队）</option>
          <option value="G-B1">第三水文组（地质二大队）</option>
        </select>
      </label>
      <label class="filter-item">
        <span>操作时间（留空取服务端时间）</span>
        <input v-model="transferForm.op_time" placeholder="2026-09-30T10:00:00" />
      </label>
      <button class="btn primary" type="submit">执行转组</button>
    </form>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">可见观测点</span>
        <strong class="stat-value">{{ rows.length }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">我可操作</span>
        <strong class="stat-value">{{ editableCount }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">授权版本</span>
        <strong class="stat-value">v{{ session.authVersion }}</strong>
      </article>
    </div>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>当前项目组</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td>{{ row.所属项目组名称 }}<br /><small class="muted">{{ row.归属来源 }}</small></td>
          <td class="row-actions">
            <template v-if="row.can_edit">
              <button
                v-for="action in actions"
                :key="action"
                class="link"
                type="button"
                @click="runAction(action, row)"
              >
                {{ action }}
              </button>
            </template>
            <span v-else class="muted">{{ session.isReadonly ? '只读账号' : '非本组不可操作' }}</span>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 2" class="empty-state">本单位下暂无水文观测数据</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条水文观测记录（台账口径与工作台、钻孔引用页一致）</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { postJson, sendJson, type ApiError } from '@/api/client'
import { useSessionStore } from '@/stores/session'

type Row = Record<string, string | number | boolean | null | undefined> & {
  id: number
  观测编号: string
  观测类型: string
  所在钻孔: string
  所属项目组名称: string
  归属来源: string
  can_edit: boolean
}

const router = useRouter()
const session = useSessionStore()

const columns = ['观测编号', '观测类型', '所在钻孔', '静止水位', '降深', '出水量', '观测日期', '观测状态']
const actions = ['记录观测', '标记异常', '复核数据']

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const showTransfer = ref(false)
const transferForm = ref({ obs_type: '稳定流抽水试验', to_group: 'G-A2', op_time: '' })

const editableCount = computed(() => rows.value.filter((row) => row.can_edit).length)

function isStale(error: ApiError) {
  return error.code === 'permission_stale' || error.code === 'session_gone' || error.status === 401
}

async function handleAuthError(error: unknown) {
  const apiError = error as ApiError
  if (isStale(apiError)) {
    errorMessage.value = `${apiError.message} 正在回到工作台重新取归属…`
    try {
      await session.loadWorkbench()
    } catch {
      session.logout()
      await router.replace('/login')
    }
    return
  }
  errorMessage.value = apiError.message || '操作失败'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    await postJson(`/api/hydro/${row.id}/actions`, { values: { action } })
    await reload()
  } catch (error) {
    await handleAuthError(error)
  }
}

async function doTransfer() {
  errorMessage.value = ''
  const payload = {
    obs_type: transferForm.value.obs_type.trim(),
    to_group: transferForm.value.to_group,
    op_time: transferForm.value.op_time.trim() || null,
  }
  try {
    await postJson('/api/auth/transfer', payload)
    showTransfer.value = false
    await session.loadWorkbench()
    await reload()
  } catch (error) {
    await handleAuthError(error)
  }
}

async function reload() {
  errorMessage.value = ''
  try {
    const payload = await sendJson<{ items: Row[]; total: number }>('/api/auth/hydro-ledger')
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    await handleAuthError(error)
  }
}

onMounted(reload)
</script>

<style scoped>
.transfer-bar {
  display: flex;
  gap: 12px;
  align-items: flex-end;
  background: #fff;
  border: 1px dashed var(--brand);
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 12px;
}
.transfer-bar input,
.transfer-bar select {
  padding: 6px;
  border: 1px solid var(--border);
  border-radius: 6px;
}
.muted { color: var(--muted); font-size: 12px; }
</style>
