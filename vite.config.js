import {defineConfig,loadEnv} from 'vite';
export default defineConfig(({mode})=>{const env=loadEnv(mode,process.cwd(),'');return {build:{outDir:'dist/client'},server:{proxy:{'/api':{target:env.RAMOS_API_URL||'http://127.0.0.1:8765',headers:env.RAMOS_API_KEY?{Authorization:`Bearer ${env.RAMOS_API_KEY}`}:{}}}}};});
