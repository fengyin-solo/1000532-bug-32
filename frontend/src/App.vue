<template>
  <div class="app-shell">
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
        <span class="head-desc">面向地质勘探的钻孔编录、岩心取样、物探数据、化探分析、测绘资料与储量估算的综合数据管理后台。</span>
        <span class="head-user">
          <label class="account-picker">
            值班账号：
            <select :value="store.selectedId" @change="switchAccount(($event.target as HTMLSelectElement).value)">
              <option value="" disabled>选择账号</option>
              <option v-for="account in store.accounts" :key="account.id" :value="account.id">
                {{ account.名称 }}（{{ roleLabel(account.角色) }}）
              </option>
            </select>
          </label>
          <em v-if="store.account" class="account-scope">{{ store.account.单位 }}</em>
        </span>
      </header>
      <div v-if="store.staleMessage" class="stale-banner">
        ⚠ {{ store.staleMessage }}，请回到「运营概览/工作台」重新进入后再操作。
      </div>
      <RouterView />
    </main>
  </div>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'

import { useSessionStore } from '@/stores/session'

const store = useSessionStore()

const navItems = [{ label: "运营概览/工作台", path: "/" }, { label: "钻孔编录", path: "/borehole" }, { label: "岩心管理", path: "/core" }, { label: "地层划分", path: "/stratigraphy" }, { label: "地球物理", path: "/geophysics" }, { label: "化探分析", path: "/geochem" }, { label: "化验数据", path: "/assay" }, { label: "地质填图", path: "/mapping" }, { label: "测绘控制", path: "/survey_point" }, { label: "钻探日志", path: "/drilling_log" }, { label: "储量估算", path: "/reserve" }, { label: "样品登记", path: "/sample_registry" }, { label: "勘探设备", path: "/equipment" }, { label: "水文地质", path: "/hydro" }, { label: "剖面编录", path: "/section" }, { label: "地质报告", path: "/geological_report" }, { label: "遥感解译", path: "/remote" }, { label: "矿产评价", path: "/mineral" }, { label: "环境地质", path: "/environmental" }]

function roleLabel(role: string) {
  return role === 'admin' ? '管理员' : role === 'viewer' ? '只读' : '经办人'
}

async function switchAccount(accountId: string) {
  store.selectAccount(accountId)
  if (accountId) {
    await store.bootstrap(accountId)
  }
}

onMounted(async () => {
  await store.loadAccounts()
  // 记住账号时进入即重新引导（重新取归属）；票据本身不缓存
  if (store.selectedId && !store.isBootstrapped) {
    try {
      await store.bootstrap()
    } catch {
      /* 账号可能已撤权，留在选择态 */
    }
  }
})
</script>

<style scoped>
.account-picker {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-style: normal;
}
.account-picker select {
  padding: 2px 6px;
}
.account-scope {
  margin-left: 10px;
  font-size: 12px;
  opacity: 0.75;
}
.stale-banner {
  margin: 10px 0;
  padding: 8px 12px;
  border: 1px solid #d48806;
  background: #fffbe6;
  color: #ad6800;
  border-radius: 6px;
  font-size: 13px;
}
</style>
