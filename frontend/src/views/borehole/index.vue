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
            <button class="link" type="button" @click="openReferences(row)">水文引用</button>
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

    <div v-if="refsPanel" class="drawer-mask" @click.self="refsPanel = null">
      <aside class="drawer">
        <header class="drawer-head">
          <h3>钻孔引用页 · {{ refsPanel.钻孔编号 }}</h3>
          <button class="btn ghost" type="button" @click="refsPanel = null">关闭</button>
        </header>
        <p class="form-hint">
          钻孔属组：<strong>{{ store.groupName(refsPanel.项目组) }}</strong>。跨组水文点以共享引用展示，
          归属与转组结论均回填自观测台账，三处口径统一。
        </p>
        <table class="data-table compact">
          <thead>
            <tr><th>观测编号</th><th>观测类型</th><th>归属项目组</th><th>引用性质</th><th>转组结论</th></tr>
          </thead>
          <tbody>
            <tr v-for="(ref, idx) in refsPanel.共享水文引用" :key="idx">
              <td>{{ ref.观测编号 }}</td>
              <td>{{ ref.观测类型 || '—' }}</td>
              <td>{{ store.groupName(ref.项目组) }}</td>
              <td>
                <span :class="ref.跨组共享 ? 'tag shared' : 'tag own'">
                  {{ ref.跨组共享 ? '跨组共享' : '本组' }}
                </span>
              </td>
              <td>{{ ref.转组结论 || '—' }}</td>
            </tr>
            <tr v-if="!refsPanel.共享水文引用.length">
              <td colspan="5" class="empty-state">该钻孔暂无水文观测引用</td>
            </tr>
          </tbody>
        </table>
      </aside>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'
import { useSessionStore } from '@/stores/session'

type Row = Record<string, string | number | null>
type HydroRef = {
  观测编号: string
  观测类型: string
  项目组: string
  跨组共享: boolean
  转组结论: string
}
type RefsPanel = { 钻孔编号: string; 项目组: string; 共享水文引用: HydroRef[] }

const store = useSessionStore()
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
const refsPanel = ref<RefsPanel | null>(null)

async function openReferences(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/hydro-references`)
    if (!response.ok) {
      throw new Error('钻孔引用读取失败')
    }
    refsPanel.value = (await response.json()) as RefsPanel
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '钻孔引用读取失败'
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

onMounted(reload)
</script>

<style scoped>
.drawer-mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.35);
  display: flex;
  justify-content: flex-end;
  z-index: 50;
}
.drawer {
  width: 720px;
  max-width: 94vw;
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
.form-hint {
  font-size: 13px;
  color: #555;
  margin-bottom: 12px;
}
.compact {
  font-size: 12px;
}
.tag {
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 12px;
}
.tag.shared {
  background: #fff7e6;
  color: #d46b08;
  border: 1px solid #ffd591;
}
.tag.own {
  background: #f6ffed;
  color: #389e0d;
  border: 1px solid #b7eb8f;
}
</style>
