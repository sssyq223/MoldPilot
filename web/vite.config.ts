import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'node:path'

export default defineConfig(({mode}) => {
  const env=loadEnv(mode,process.cwd(),'')
  const pack=(env.VITE_BUSINESS_PACK||'mold').trim()
  const apiTarget=env.VITE_API_PROXY_TARGET||'http://127.0.0.1:8001'
  if(!/^https?:\/\//.test(apiTarget))throw new Error('VITE_API_PROXY_TARGET must be an HTTP(S) API origin')
  if(!/^[a-z][a-z0-9_]*$/.test(pack))throw new Error('VITE_BUSINESS_PACK must name an installed domain pack')
  return {
    plugins: [vue()],
    define:{__DOMAIN_PACK_ID__:JSON.stringify(pack)},
    resolve:{alias:{'@domain-pack':resolve(__dirname,`src/domain-packs/${pack}`)}},
    server: {
      host: '0.0.0.0',
      hmr: { host: '127.0.0.1', clientPort: 5173 },
      proxy: { '/api': apiTarget },
    },
  }
})
