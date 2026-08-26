import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from './App.vue'
import { i18n } from './i18n'
import router from './router'
import './style.css'

const app = createApp(App)
const pinia = createPinia()

app.use(pinia).use(i18n).use(router)

async function bootstrap() {
  await router.isReady()
  app.mount('#app')
}

void bootstrap()

