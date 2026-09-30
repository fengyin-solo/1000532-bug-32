<template>
  <section class="page">
    <header class="page-head">
      <div>
        <h2>运营概览 / 工作台</h2>
        <p class="page-desc">每次进入工作台都重新引导会话并重新取一次归属；转组、撤权后缓存的旧任务一律以本清单为准。</p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" :disabled="bootstrapping" @click="reenter">
          {{ bootstrapping ? '重新引导中…' : '重新进入工作台' }}
        </button>
      </div>
    </header>

    <div v-if="!store.account" class="entry-hint">
      请在右上角选择值班账号进入工作台；已撤权账号无法建立会话。
    </div>

    <template v-else>
      <div class="stat-row">
        <article class="stat-card">
          <span class="stat-label">当前账号</span>
          <strong class="stat-value">{{ store.account.名称 }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">单位</span>
          <strong class="stat-value">{{ store.account.单位 }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">角色</span>
          <strong class="stat-value">{{ roleLabel }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">待处理水文任务</span>
          <strong class="stat-value">{{ pendingCount }}</strong>
        </article>
      </div>

      <h3 class="section-title">我的水文观测任务（归属以当前项目组为准）</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>观测编号</th>
            <th>观测类型</th>
            <th>所在钻孔</th>
            <th>状态</th>
            <th>当前归属项目组</th>
            <th>单位</th>
            <th>转组结论</th>
            <th>口径</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in tasks" :key="String(row.id)">
            <td>
              <RouterLink :to="`/hydro?focus=${row.id}`">{{ row.观测编号 }}</RouterLink>
            </td>
            <td>{{ row.观测类型 || '—' }}</td>
            <td>{{ row.所在钻孔 }}</td>
            <td>{{ row.status }}</td>
            <td>{{ row.项目组名称 }}</td>
            <td>{{ row.所属单位 }}</td>
            <td>{{ row.转组结论 || '—' }}</td>
            <td>
              <span :class="row.可写 ? 'tag writable' : 'tag readonly'">{{ row.可写 ? '可提交' : '只读' }}</span>
            </td>
          </tr>
          <tr v-if="!tasks.length">
            <td colspan="8" class="empty-state">本单位暂无可见水文任务</td>
          </tr>
        </tbody>
      </table>

      <h3 class="section-title">全模块指标</h3>
      <table class="data-table">
        <thead>
          <tr><th>业务模块</th><th>今日新增</th><th>待处理</th><th>异常量</th></tr>
        </thead>
        <tbody>
          <tr v-for="row in moduleRows" :key="row.name">
            <td>{{ row.name }}</td>
            <td>{{ row.created }}</td>
            <td>{{ row.pending }}</td>
            <td>{{ row.abnormal }}</td>
          </tr>
        </tbody>
      </table>
      <footer class="page-foot">
        <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
        <span v-else>权限版本 {{ workbenchEpoch }}，清单为实时口径</span>
      </footer>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { fetchJson, request, SessionStaleError } from '@/api/client'
import { useSessionStore } from '@/stores/session'

type WorkbenchTask = {
  id: number
  观测编号: string
  观测类型: string
  所在钻孔: string
  status: string
  pending: boolean
  项目组: string
  项目组名称: string
  所属单位: string
  转组结论: string
  可写: boolean
}

type Overview = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

const store = useSessionStore()
const tasks = ref<WorkbenchTask[]>([])
const workbenchEpoch = ref(0)
const moduleRows = ref<Overview['modules']>([])
const errorMessage = ref('')
const bootstrapping = ref(false)

const pendingCount = computed(() => tasks.value.filter((item) => item.pending).length)
const roleLabel = computed(() =>
  store.account?.角色 === 'admin' ? '平台管理员' : store.account?.角色 === 'viewer' ? '只读账号' : '经办人',
)

/**
 * 重新进入工作台：
 * 1) 重新 bootstrap——重新取一次归属，旧票据（转组/撤权残留）直接换新；
 * 2) 再拉工作台清单——权限口径以这一版为准，本地不缓存任务可写性。
 */
async function reenter() {
  errorMessage.value = ''
  bootstrapping.value = true
  try {
    if (!store.selectedId) {
      throw new Error('请先选择值班账号')
    }
    await store.bootstrap()
    const response = await request('/api/workbench')
    const payload = (await response.json()) as { items: WorkbenchTask[]; token_epoch: number }
    tasks.value = payload.items ?? []
    workbenchEpoch.value = payload.token_epoch
    const overview = await fetchJson<Overview>('/api/overview').catch(() => null)
    moduleRows.value = overview?.modules ?? []
  } catch (error) {
    errorMessage.value =
      error instanceof SessionStaleError ? error.message : error instanceof Error ? error.message : '工作台加载失败'
  } finally {
    bootstrapping.value = false
  }
}

onMounted(reenter)
</script>

<style scoped>
.entry-hint {
  padding: 24px;
  text-align: center;
  color: #7a7a7a;
  border: 1px dashed #d0d0d0;
  border-radius: 8px;
}
.section-title {
  margin: 22px 0 10px;
  font-size: 15px;
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
