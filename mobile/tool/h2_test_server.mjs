import fs from 'node:fs';
import http2 from 'node:http2';
import https from 'node:https';
import zlib from 'node:zlib';
const [certificate, privateKey]=process.argv.slice(2);
const options={cert:fs.readFileSync(certificate),key:fs.readFileSync(privateKey),minVersion:'TLSv1.3',maxVersion:'TLSv1.3'};
const counts={login:0,verify:0,drop:0,redirect:0,registration:0,h2:0,http1:0};
const cookies=[];
let serverError='';
const server=http2.createSecureServer({...options,allowHTTP1:false});
server.on('sessionError',()=>{});server.on('tlsClientError',()=>{});
server.on('stream',(stream,headers)=>{
 stream.on('error',()=>{});
 const url=new URL(headers[':path'],'https://localhost');
 if(url.pathname==='/metrics') {stream.respond({':status':200,'content-type':'application/json'});stream.end(JSON.stringify({...counts,serverError}));return;}
 if(stream.session.socket.alpnProtocol!=='h2' || stream.session.socket.getProtocol()!=='TLSv1.3') serverError='unexpected protocol';
 counts.h2++;
 let raw='';stream.setEncoding('utf8');stream.on('data',chunk=>raw+=chunk);
 stream.on('end',()=>{
  if(headers.connection || headers['transfer-encoding'] || headers['proxy-connection']) serverError='HTTP/1 connection header in HTTP/2';
  if(headers[':method']==='POST' && Number(headers['content-length'])!==Buffer.byteLength(raw)) serverError='wrong form length';
  if(headers[':method']==='POST' && headers['content-type']!=='application/x-www-form-urlencoded; charset=UTF-8') serverError='wrong form type';
  const body=Object.fromEntries(new URLSearchParams(raw));
  let result={status:'ok'};
  if(url.pathname==='/drop') {counts.drop++;stream.close(http2.constants.NGHTTP2_REFUSED_STREAM);return;}
  if(url.pathname==='/redirect') {counts.redirect++;stream.respond({':status':302,location:'https://invalid.example/do-not-follow'});stream.end();return;}
  if(url.pathname==='/graphql_www') {counts.registration++;result={data:{usdid_registration:{success:true}}};}
  else if(url.pathname.includes('process_client_data')) {
   result={layout:{bloks_payload:{data:[{data:{key:'CAA_ACCOUNT_ACCESS_CONTEXT:aac',initial:'server-aac'}}]}}};
   cookies.push('mid=machine; Domain=.instagram.com; Path=/; Secure; HttpOnly','csrftoken=csrf; Domain=.instagram.com; Path=/; Secure; HttpOnly');
  } else if(url.pathname.includes('attestation')) result={challenge_nonce:'nonce'};
  else if(url.pathname.includes('launcher')) {
   stream.respond({':status':200,'content-type':'application/json','ig-set-password-encryption-pub-key':'key','ig-set-password-encryption-key-id':'1'});stream.end(JSON.stringify(result));return;
  } else if(url.pathname.includes('send_login_request')) {
   counts.login++;
   const params=JSON.parse(body.params);
   if(params.client_input_params.machine_id!=='machine') serverError='MID cookie lost';
   if(!headers.cookie?.includes('mid=machine')) serverError='cookie header lost';
   if(params.client_input_params.password!=='#PWD_INSTAGRAM:4:0:encrypted') serverError='plaintext password';
   if(url.searchParams.get('mode')==='rate') {stream.respond({':status':429,'content-type':'text/html','retry-after':'120'});stream.end('<html>Too many requests</html>');return;}
   result={two_step_verification_context:'ctx'};
  } else if(url.pathname.includes('verify_code')) {
   counts.verify++;
   if(JSON.parse(body.params).client_input_params.code!=='123456') serverError='wrong verification code';
   const envelope={login_response:JSON.stringify({status:'ok',logged_in_user:{pk:'123'}}),headers:JSON.stringify({'IG-Set-Authorization':'Bearer IGT:2:'+Buffer.from(JSON.stringify({ds_user_id:'123',sessionid:'synthetic-session'})).toString('base64')}),cookies:''};
   result={layout:{bloks_payload:{action:`(action ${JSON.stringify(JSON.stringify(envelope))})`}}};
  }
  const fields={':status':200,'content-type':'application/json','content-encoding':'gzip'};
  if(cookies.length) fields['set-cookie']=cookies.splice(0);
  stream.respond(fields);stream.end(zlib.gzipSync(Buffer.from(JSON.stringify(result))));
 });
});
const legacy=https.createServer({...options,ALPNProtocols:['http/1.1']},(req,res)=>{counts.http1++;res.end('{}');});
legacy.on('tlsClientError',()=>{});
await Promise.all([new Promise(resolve=>server.listen(0,'127.0.0.1',resolve)),new Promise(resolve=>legacy.listen(0,'127.0.0.1',resolve))]);
console.log(JSON.stringify({h2Port:server.address().port,http1Port:legacy.address().port}));
process.on('SIGTERM',()=>{server.close();legacy.close();process.exit(0);});
