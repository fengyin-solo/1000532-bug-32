<template>
  <section class="login-page">
    <div class="login-card">
      <h2>地质勘探数据管理平台</h2>
      <p class="page-desc">请选择账号登录；归属以当前项目组为准，跨单位操作将被拒绝。</p>
      <label class="filter-item">
        <span>值班账号</span>
        <select v-model="username">
          <option v-for="account in DEMO_ACCOUNTS" :key="account.value" :value="account.value">
            {{ account.label }}
          </option>
        </select>
      </label>
      <button class="btn primary" type="button" :disabled="loading" @click="doLogin">
        {{ loading ? '登录中…' : '进入工作台' }}
      </button>
      <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>
    </div>
  </section>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'

import { DEMO_ACCOUNTS, useSessionStore } from '@/stores/session'

const router = useRouter()
const session = useSessionStore()
const username = ref(DEMO_ACCOUNTS[1].value)
const loading = ref(false)
const errorMessage = ref('')

async function doLogin() {
  loading.value = true
  errorMessage.value = ''
  try {
    await session.login(username.value)
    await router.replace('/')
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登录失败，请重试'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f6f8fb;
}
.login-card {
  width: 420px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.login-card select {
  width: 100%;
  padding: 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
}
</style>
