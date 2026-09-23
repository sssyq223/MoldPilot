import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'node:path'

export default defineConfig(({mode}) => {
  const pack=(loadEnv(mode,process.cwd(),'').VITE_BUSINESS_PACK||'mold').trim()
  if(!/^[a-z][a-z0-9_]*$/.test(pack))throw new Error('VITE_BUSINESS_PACK must name an installed domain pack')
  return {
    plugins: [vue()],
    define:{__DOMAIN_PACK_ID__:JSON.stringify(pack)},
    resolve:{alias:{'@domain-pack':resolve(__dirname,`src/domain-packs/${pack}`)}},
    server: {
      proxy: {
        '/api': {
          target: 'http://127.0.0.1:8000',
          timeout: 0,
          proxyTimeout: 0,
        },
      },
    },
  }
})
