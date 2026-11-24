import fs from 'node:fs';
import {spawn} from 'node:child_process';
const python=process.env.RAMOS_PYTHON || (process.platform==='win32'?'.venv/Scripts/python.exe':fs.existsSync('.venv/bin/python')?'.venv/bin/python':'python3');
const backend=spawn(python,['backend/app.py'],{stdio:'inherit',env:process.env});
const frontend=spawn(process.execPath,['node_modules/vite/bin/vite.js','--host','0.0.0.0',...process.argv.slice(2)],{stdio:'inherit',env:process.env});
let closing=false;
function stop(code=0){if(closing)return;closing=true;backend.kill();frontend.kill();process.exit(code);}
backend.on('error',()=>stop(1));frontend.on('exit',code=>stop(code||0));
process.on('SIGTERM',()=>stop());process.on('SIGINT',()=>stop());
