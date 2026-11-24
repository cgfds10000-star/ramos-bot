import fs from 'node:fs';
import path from 'node:path';
const root='dist/client';
const files={};
const types={'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.js':'text/javascript; charset=utf-8','.svg':'image/svg+xml','.png':'image/png','.ttf':'font/ttf','.txt':'text/plain; charset=utf-8','.webmanifest':'application/manifest+json'};
function walk(dir){for(const name of fs.readdirSync(dir)){const file=path.join(dir,name);if(fs.statSync(file).isDirectory())walk(file);else files['/'+path.relative(root,file).split(path.sep).join('/')]={body:fs.readFileSync(file).toString('base64'),type:types[path.extname(file)]||'application/octet-stream'};}}
walk(root);
const prefix='const assets='+JSON.stringify(files)+';\n';
const handler=fs.readFileSync('scripts/worker-handler.js','utf8');
fs.mkdirSync('dist/server',{recursive:true});fs.writeFileSync('dist/server/index.js',prefix+handler);
console.log('React + Worker: '+Object.keys(files).length+' arquivos.');
