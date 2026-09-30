<template>
  <div v-if="session.loggedIn" class="app-shell">
    <aside class="app-side">
      <h1 class="app-title">地质勘探数据管理平台</h1>
      <nav class="nav-list">
        <RouterLink v-for="item in navItems" :key="item.path" :to="item.path" class="nav-item">
          {{ item.label }}
        </RouterLink>
      </nav>
    </aside>
    <main class="app-main">
      <header class="app-head">
        <span class="head-desc">归属以当前项目组为准；跨单位提交一律拒绝，只读账号不能改动观测数据。</span>
        <span class="head-user">
          {{ session.operator?.name }} · 单位 {{ session.operator?.unit }} ·
          {{ roleLabel }} · 授权版本 v{{ session.authVersion }}
          <button class="btn tiny" type="button" @click="doLogout">退出</button>
        </span>
      </header>
      <RouterView />
    </main>
  </div>
  <RouterView v-else />
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'

import { getToken, postJson } from '@/api/client'
import { useSessionStore } from '@/stores/session'

const router = useRouter()
const session = useSessionStore()

const navItems = [{ label: "运营概览", path: "/" }, { label: "钻孔编录", path: "/borehole" }, { label: "岩心管理", path: "/core" }, { label: "地层划分", path: "/stratigraphy" }, { label: "地球物理", path: "/geophysics" }, { label: "化探分析", path: "/geochem" }, { label: "化验数据", path: "/assay" }, { label: "地质填图", path: "/mapping" }, { label: "测绘控制", path: "/survey_point" }, { label: "钻探日志", path: "/drilling_log" }, { label: "储量估算", path: "/reserve" }, { label: "样品登记", path: "/sample_registry" }, { label: "勘探设备", path: "/equipment" }, { label: "水文地质", path: "/hydro" }, { label: "剖面编录", path: "/section" }, { label: "地质报告", path: "/geological_report" }, { label: "遥感解译", path: "/remote" }, { label: "矿产评价", path: "/mineral" }, { label: "环境地质", path: "/environmental" }]

const roleLabel = session.isAdmin ? '管理员' : session.isReadonly ? '只读' : '观测员'

onMounted(async () => {
  const token = getToken()
  if (token && !session.token) {
    session.hydrate(token)
    try {
      // 重新进入工作台：重新取一次归属
      await session.loadWorkbench()
    } catch {
      session.logout()
      await router.replace('/login')
    }
  }
})

async function doLogout() {
  try {
    await postJson('/api/auth/logout', {})
  } catch {
    /* 服务端会话即便已失效，前端也要清干净 */
  }
  session.logout()
  await router.replace('/login')
}
</script>

<style scoped>
.btn.tiny { padding: 2px 8px; font-size: 12px; margin-left: 8px; }
</style>
