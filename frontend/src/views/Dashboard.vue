<template>
  <section class="page">
    <header class="page-head">
      <div>
        <h2>工作台</h2>
        <p class="page-desc">
          归属清单每次进入都重新向服务端取一次；本地缓存的旧任务在权限更新后无法提交。
          <span v-if="session.workbenchLoadedAt">最近取数：{{ session.workbenchLoadedAt }}</span>
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" :disabled="loading" @click="reload">重新取归属</button>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">本单位可见观测点</span>
        <strong class="stat-value">{{ rows.length }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">我可操作</span>
        <strong class="stat-value">{{ session.editablePoints.length }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">当前授权版本</span>
        <strong class="stat-value">v{{ session.authVersion }}</strong>
      </article>
    </div>

    <p v-if="notice" class="notice">{{ notice }}</p>

    <table class="data-table">
      <thead>
        <tr>
          <th>观测编号</th>
          <th>观测类型</th>
          <th>所在钻孔</th>
          <th>观测状态</th>
          <th>当前项目组</th>
          <th>所属单位</th>
          <th>归属来源</th>
          <th>我的权限</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td>{{ row.观测编号 }}</td>
          <td>{{ row.观测类型 }}</td>
          <td>{{ row.所在钻孔 }}</td>
          <td>{{ row.观测状态 ?? row.status ?? '—' }}</td>
          <td>{{ row.所属项目组名称 }}</td>
          <td>{{ row.所属单位 }}</td>
          <td>{{ row.归属来源 }}</td>
          <td>
            <span v-if="row.can_edit" class="tag ok">可操作</span>
            <span v-else class="tag no">只读/非本组</span>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td colspan="8" class="empty-state">当前单位下暂无可见水文观测点</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ rows.length }} 条；口径与观测台账、钻孔引用页一致</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'

import { useSessionStore } from '@/stores/session'

const session = useSessionStore()
const loading = ref(false)
const errorMessage = ref('')
const notice = ref('')

const rows = computed(() => session.workbench)

async function reload() {
  loading.value = true
  errorMessage.value = ''
  notice.value = ''
  try {
    const result = await session.loadWorkbench()
    notice.value = `已按当前项目组重新取归属（授权版本 v${result.auth_version}），缓存清单已整体刷新`
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '工作台取数失败'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.notice { color: #176932; font-size: 13px; }
.tag { padding: 2px 8px; border-radius: 10px; font-size: 12px; }
.tag.ok { background: #e7f6ec; color: #176932; }
.tag.no { background: #f1f3f7; color: var(--muted); }
</style>
