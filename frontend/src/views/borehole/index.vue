<template>
  <section class="page" data-module="borehole">
    <header class="page-head">
      <div>
        <h2>钻孔编录管理</h2>
        <p class="page-desc">维护钻孔，围绕钻孔编号、勘探区、孔口坐标、设计孔深做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记钻孔</button>
        <button class="btn" type="button" @click="exportRows">导出钻孔编录清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

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
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无钻孔编录数据，可先登记钻孔</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条钻孔编录记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <section class="ref-panel">
      <header class="ref-head">
        <h3>钻孔水文引用回填</h3>
        <div class="ref-query">
          <input v-model="refCode" placeholder="输入钻孔编号，如 BORE-0001" />
          <button class="btn" type="button" @click="loadRefs">回填引用</button>
        </div>
      </header>
      <p class="muted" v-if="!refRows.length && !refLoading">
        共享引用按观测类型当前项目组回填，与观测台账、工作台口径一致。
      </p>
      <table v-else class="data-table">
        <thead>
          <tr>
            <th>观测编号</th>
            <th>观测类型</th>
            <th>观测状态</th>
            <th>当前项目组</th>
            <th>归属来源</th>
            <th>我的权限</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in refRows" :key="String(row.id)">
            <td>{{ row.观测编号 }}</td>
            <td>{{ row.观测类型 }}</td>
            <td>{{ row.观测状态 ?? row.status ?? '—' }}</td>
            <td>{{ row.所属项目组名称 }}</td>
            <td>{{ row.归属来源 }}</td>
            <td>{{ row.can_edit ? '可操作' : '只读/非本组' }}</td>
          </tr>
        </tbody>
      </table>
      <span v-if="refError" class="error-text">{{ refError }}</span>
    </section>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request, sendJson } from '@/api/client'

type Row = Record<string, string | number | boolean | null>

type HydroRef = {
  id: number
  观测编号: string
  观测类型: string
  观测状态?: string
  status?: string
  所属项目组名称: string
  归属来源: string
  can_edit: boolean
}

const ENDPOINT = '/api/borehole'
const columns = ["钻孔编号", "勘探区", "孔口坐标", "设计孔深", "终孔深度", "开孔日期", "终孔日期", "钻孔状态"]
const actions = ["开始钻进", "登记终孔", "执行封孔"]
const statuses = ["待施工", "钻进中", "已终孔", "已封孔", "已废弃"]
const stats = [{"label": "施工中钻孔", "value": 0}, {"label": "已终孔钻孔", "value": 0}, {"label": "已封孔钻孔", "value": 0}]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

// 钻孔引用页：共享水文引用回填
const refCode = ref('BORE-0001')
const refRows = ref<HydroRef[]>([])
const refLoading = ref(false)
const refError = ref('')

async function loadRefs() {
  refLoading.value = true
  refError.value = ''
  try {
    const payload = await sendJson<{ items: HydroRef[] }>(
      `/api/auth/borehole-refs/${encodeURIComponent(refCode.value.trim())}`,
    )
    refRows.value = payload.items ?? []
  } catch (error) {
    refRows.value = []
    refError.value = error instanceof Error ? error.message : '引用回填失败'
  } finally {
    refLoading.value = false
  }
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '钻孔登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('钻孔编录动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '钻孔编录操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('钻孔列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '钻孔编录列表读取失败'
  }
}

onMounted(() => {
  void reload()
  void loadRefs()
})
</script>

<style scoped>
.ref-panel {
  margin-top: 18px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 14px;
}
.ref-head { display: flex; justify-content: space-between; align-items: center; }
.ref-head h3 { margin: 0; font-size: 15px; }
.ref-query { display: flex; gap: 8px; }
.ref-query input { padding: 6px; border: 1px solid var(--border); border-radius: 6px; }
.muted { color: var(--muted); font-size: 12px; }
</style>
