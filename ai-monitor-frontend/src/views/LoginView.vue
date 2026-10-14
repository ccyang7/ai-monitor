<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { extractErrorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const mode = ref<'login' | 'register'>('login')
const submitting = ref(false)
const showPassword = ref(false)
const formError = ref('')

const form = reactive({
  username: '',
  password: '',
  email: '',
})

const errors = reactive<{ username?: string; password?: string; email?: string }>({})

function clearErrors(): void {
  formError.value = ''
  errors.username = undefined
  errors.password = undefined
  errors.email = undefined
}

function validate(): boolean {
  clearErrors()
  const username = form.username.trim()

  if (username.length < 3) {
    errors.username = '用户名至少 3 个字符'
  } else if (username.length > 50) {
    errors.username = '用户名最多 50 个字符'
  }

  // bcrypt 的硬上限是 72【字节】不是字符，一个汉字占 3 字节。
  // 不在这里挡，请求会走到后端才被拒，用户看到一个 422 而不是这句人话
  const passwordBytes = new TextEncoder().encode(form.password).length
  if (form.password.length < 6) {
    errors.password = '密码至少 6 个字符'
  } else if (passwordBytes > 72) {
    errors.password = '密码过长：UTF-8 编码后不能超过 72 字节（一个汉字算 3 字节）'
  }

  if (mode.value === 'register' && form.email.trim().length > 255) {
    errors.email = '邮箱最多 255 个字符'
  }

  return !errors.username && !errors.password && !errors.email
}

function switchMode(): void {
  mode.value = mode.value === 'login' ? 'register' : 'login'
  clearErrors()
}

async function submit(): Promise<void> {
  if (submitting.value || !validate()) return

  submitting.value = true
  const username = form.username.trim()
  const email = form.email.trim()

  try {
    if (mode.value === 'login') {
      await auth.login({ username, password: form.password })
    } else {
      await auth.register({ username, password: form.password, email: email || null })
    }

    // 只接受站内路径。放行外部地址等于把登录页变成开放重定向的跳板
    const redirect = route.query.redirect
    const target =
      typeof redirect === 'string' && redirect.startsWith('/') ? redirect : null
    await router.replace(target ?? { name: 'home' })
  } catch (error) {
    formError.value = extractErrorMessage(error)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="auth">
    <section class="auth-brand">
      <div class="auth-logo">
        <div class="auth-logo-mark">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"
               stroke-linecap="round" stroke-linejoin="round">
            <path d="M2 12h3.5l2.5-6 4 13 2.5-7h7.5" />
          </svg>
        </div>
        <div>
          <div class="auth-logo-name">AI Monitor</div>
          <div class="auth-logo-sub">大模型使用监控</div>
        </div>
      </div>

      <h1 class="auth-pitch">你的模型今天跑了多少、慢在哪、哪里不对劲。</h1>

      <div class="sample">
        <div class="sample-stamp">
          <span class="dot" />
          <span>接入项目后，这里是你每天看到的简报</span>
        </div>
        <p class="sample-text">
          今天调用 <b>145 次</b>，比昨天多 32%。deepseek-chat 占 <b>82%</b>，费用 ¥12.40。有 <b>2 次异常</b>调用已标出。
        </p>
      </div>
    </section>

    <section class="auth-form">
      <div class="auth-form-inner">
        <h2 class="auth-form-title">{{ mode === 'login' ? '登录' : '注册' }}</h2>
        <p class="auth-form-sub">
          {{ mode === 'login' ? '用你的账号继续' : '创建一个账号，开始监控你的 AI 项目' }}
        </p>

        <div v-if="formError" class="auth-error is-on">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
               stroke-linecap="round">
            <circle cx="12" cy="12" r="9" />
            <path d="M12 8v4.5M12 16h.01" />
          </svg>
          <span>{{ formError }}</span>
        </div>

        <form class="auth-fields" novalidate @submit.prevent="submit">
          <div class="field">
            <label class="field-label" for="username">用户名</label>
            <input
              id="username"
              v-model="form.username"
              class="input"
              :class="{ 'is-invalid': errors.username }"
              type="text"
              placeholder="输入用户名"
              autocomplete="username"
            />
            <span v-if="errors.username" class="field-error">{{ errors.username }}</span>
          </div>

          <div class="field">
            <label class="field-label" for="password">密码</label>
            <div class="pw-wrap">
              <input
                id="password"
                v-model="form.password"
                class="input"
                :class="{ 'is-invalid': errors.password }"
                :type="showPassword ? 'text' : 'password'"
                placeholder="输入密码"
                :autocomplete="mode === 'login' ? 'current-password' : 'new-password'"
              />
              <button
                class="pw-toggle"
                type="button"
                :aria-label="showPassword ? '隐藏密码' : '显示密码'"
                @click="showPassword = !showPassword"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
                     stroke-linecap="round" stroke-linejoin="round">
                  <path d="M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12z" />
                  <circle cx="12" cy="12" r="2.6" />
                </svg>
              </button>
            </div>
            <span v-if="errors.password" class="field-error">{{ errors.password }}</span>
          </div>

          <div v-if="mode === 'register'" class="field">
            <label class="field-label" for="email">邮箱</label>
            <input
              id="email"
              v-model="form.email"
              class="input"
              :class="{ 'is-invalid': errors.email }"
              type="email"
              placeholder="选填"
              autocomplete="email"
            />
            <span v-if="errors.email" class="field-error">{{ errors.email }}</span>
          </div>

          <button class="btn btn-primary btn-block auth-submit" type="submit" :disabled="submitting">
            {{ submitting ? '请稍候…' : mode === 'login' ? '登录' : '注册并登录' }}
          </button>
        </form>

        <p class="auth-alt">
          {{ mode === 'login' ? '还没有账号？' : '已经有账号了？' }}
          <button class="link-btn" type="button" @click="switchMode">
            {{ mode === 'login' ? '注册一个' : '去登录' }}
          </button>
        </p>
      </div>
    </section>
  </div>
</template>

<style scoped>
/* 登录页没有侧边栏，所以不复用 .shell */
.auth {
  display: grid;
  grid-template-columns: 1.05fr 0.95fr;
  min-height: 100vh;
}

.auth-brand {
  position: relative;
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 56px 64px;
  background: var(--green-800);
  color: #fff;
  overflow: hidden;
}

.auth-brand::after {
  content: '';
  position: absolute;
  width: 620px;
  height: 620px;
  right: -260px;
  top: -180px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(82, 183, 136, 0.22), transparent 68%);
  pointer-events: none;
}

.auth-brand > * {
  position: relative;
  z-index: 1;
}

.auth-logo {
  display: flex;
  align-items: center;
  gap: 11px;
  margin-bottom: 54px;
}

.auth-logo-mark {
  width: 40px;
  height: 40px;
  display: grid;
  place-items: center;
  border-radius: 12px;
  background: var(--green-400);
  color: var(--green-900);
}

.auth-logo-mark svg {
  width: 21px;
  height: 21px;
}

.auth-logo-name {
  font-size: var(--fs-md);
  font-weight: 650;
}

.auth-logo-sub {
  font-size: 11px;
  color: rgba(255, 255, 255, 0.5);
  line-height: 1.3;
}

.auth-pitch {
  font-size: 32px;
  font-weight: 640;
  line-height: 1.45;
  letter-spacing: -0.01em;
  color: #fff;
  max-width: 15em;
  margin-bottom: 40px;
}

.sample {
  max-width: 400px;
  border-radius: var(--r-lg);
  background: rgba(255, 255, 255, 0.07);
  border: 1px solid rgba(255, 255, 255, 0.1);
  padding: 17px 19px;
}

.sample-stamp {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--fs-xs);
  color: rgba(255, 255, 255, 0.5);
  margin-bottom: 10px;
}

.sample-stamp .dot {
  color: var(--green-400);
}

.sample-text {
  font-size: var(--fs-base);
  line-height: 1.75;
  color: rgba(255, 255, 255, 0.82);
}

.sample-text b {
  color: #fff;
  font-weight: 650;
}

.auth-form {
  display: grid;
  place-items: center;
  padding: 48px 32px;
  background: var(--paper);
}

.auth-form-inner {
  width: 100%;
  max-width: 372px;
}

.auth-form-title {
  font-size: var(--fs-2xl);
  font-weight: 680;
  letter-spacing: -0.015em;
}

.auth-form-sub {
  color: var(--muted);
  font-size: var(--fs-base);
  margin: 5px 0 30px;
}

.auth-fields {
  display: flex;
  flex-direction: column;
  gap: 17px;
}

.pw-wrap {
  position: relative;
}

.pw-wrap .input {
  padding-right: 42px;
}

.pw-toggle {
  position: absolute;
  right: 5px;
  top: 50%;
  transform: translateY(-50%);
  width: 32px;
  height: 32px;
  display: grid;
  place-items: center;
  border: 0;
  border-radius: var(--r-sm);
  background: transparent;
  color: var(--faint);
  cursor: pointer;
}

.pw-toggle:hover {
  color: var(--green-500);
  background: var(--green-50);
}

.pw-toggle svg {
  width: 16px;
  height: 16px;
}

.auth-submit {
  margin-top: 4px;
  padding: 12px;
  font-size: var(--fs-md);
}

.auth-alt {
  margin-top: 24px;
  text-align: center;
  font-size: var(--fs-sm);
  color: var(--muted);
}

.link-btn {
  border: 0;
  background: none;
  padding: 0;
  font: inherit;
  color: var(--green-500);
  font-weight: 600;
  cursor: pointer;
}

.link-btn:hover {
  color: var(--green-600);
  text-decoration: underline;
}

.auth-error {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-bottom: 20px;
  padding: 11px 13px;
  border-radius: var(--r-md);
  background: var(--red-bg);
  color: var(--red);
  font-size: var(--fs-sm);
}

.auth-error svg {
  width: 16px;
  height: 16px;
  flex: none;
  margin-top: 2px;
}

@media (max-width: 900px) {
  .auth {
    grid-template-columns: 1fr;
  }
  .auth-brand {
    padding: 40px 28px 44px;
  }
  .auth-pitch {
    font-size: 25px;
    margin-bottom: 26px;
  }
  .auth-logo {
    margin-bottom: 32px;
  }
  .auth-form {
    padding: 40px 24px 56px;
  }
}
</style>
