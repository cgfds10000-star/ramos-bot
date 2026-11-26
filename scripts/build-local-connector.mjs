import fs from 'node:fs';
import path from 'node:path';
import {deflateRawSync} from 'node:zlib';

// ZIP com CRC32 e deflate, sem ferramentas extras no builder Node do Docker.
const table = Array.from({length:256}, (_, n) => {
  let c=n; for(let k=0;k<8;k++) c=(c&1)?0xedb88320^(c>>>1):c>>>1; return c>>>0;
});
function crc32(bytes) {
  let crc=0xffffffff; for(const b of bytes) crc=table[(crc^b)&255]^(crc>>>8); return (crc^0xffffffff)>>>0;
}
const entries=[];
for(const name of fs.readdirSync('backend').sort()) {
  if(name.endsWith('.py') || name==='requirements.txt') entries.push(['backend/'+name, fs.readFileSync(path.join('backend',name))]);
}
for(const name of ['pc_bridge.py','Conectar-PC.ps1','INICIAR-CONEXAO-PC.cmd','LEIA-ME-PC.txt']) {
  entries.push([name,fs.readFileSync(path.join('connector',name))]);
}
const local=[], central=[]; let offset=0;
for(const [relative,raw] of entries) {
  const name=Buffer.from('Ramos-Conector-PC/'+relative);
  const compressed=deflateRawSync(raw,{level:9}), crc=crc32(raw);
  const h=Buffer.alloc(30); h.writeUInt32LE(0x04034b50); h.writeUInt16LE(20,4); h.writeUInt16LE(0x800,6); h.writeUInt16LE(8,8); h.writeUInt16LE(33,12);
  h.writeUInt32LE(crc,14); h.writeUInt32LE(compressed.length,18); h.writeUInt32LE(raw.length,22); h.writeUInt16LE(name.length,26);
  const c=Buffer.alloc(46); c.writeUInt32LE(0x02014b50); c.writeUInt16LE(20,4); c.writeUInt16LE(20,6); c.writeUInt16LE(0x800,8); c.writeUInt16LE(8,10); c.writeUInt16LE(33,14);
  c.writeUInt32LE(crc,16); c.writeUInt32LE(compressed.length,20); c.writeUInt32LE(raw.length,24); c.writeUInt16LE(name.length,28); c.writeUInt32LE(offset,42);
  local.push(h,name,compressed); central.push(c,name); offset+=h.length+name.length+compressed.length;
}
const directory=Buffer.concat(central), end=Buffer.alloc(22); end.writeUInt32LE(0x06054b50); end.writeUInt16LE(entries.length,8); end.writeUInt16LE(entries.length,10); end.writeUInt32LE(directory.length,12); end.writeUInt32LE(offset,16);
fs.mkdirSync('dist/client/downloads',{recursive:true});
fs.writeFileSync('dist/client/downloads/Ramos-Conector-PC.zip', Buffer.concat([...local,directory,end]));
console.log('Conector PC: '+entries.length+' arquivos, sem dados pessoais.');
