import fs from 'node:fs';
import http2 from 'node:http2';
import https from 'node:https';
import zlib from 'node:zlib';
import {createPublicKey} from 'node:crypto';

const tls = {key: fs.readFileSync(process.argv[2]), cert: fs.readFileSync(process.argv[3]), minVersion: 'TLSv1.3'};
const counts = {};
const publicKey = createPublicKey(tls.key).export({type: 'spki', format: 'pem'}).toString();
const h2 = http2.createSecureServer(tls);
h2.on('sessionError', () => {});
h2.on('stream', (stream, headers) => {
  stream.on('error', () => {});
  const path = headers[':path'];
  counts[path] = (counts[path] || 0) + 1;
  const chunks = [];
  stream.on('data', data => chunks.push(data));
  stream.on('end', () => {
    const body = Buffer.concat(chunks).toString();
    if (path.startsWith('/api/') || path === '/graphql_www') {
      let result = {status: 'ok'};
      const extra = {};
      if (path === '/graphql_www') result = {data: {usdid_registration: {success: true}}};
      else if (path.includes('process_client_data')) result = {layout: {bloks_payload: {data: [{data: {key: 'CAA_ACCOUNT_ACCESS_CONTEXT:aac', initial: 'synthetic-aac'}}]}}};
      else if (path.includes('attestation')) result = {challenge_nonce: 'synthetic-nonce'};
      else if (path.includes('launcher')) {
        extra['ig-set-password-encryption-key-id'] = '1';
        extra['ig-set-password-encryption-pub-key'] = Buffer.from(publicKey).toString('base64');
      } else if (path.includes('send_login_request') || path.includes('two_step_verification')) {
        result = {two_step_verification_context: 'synthetic-context', totp_two_factor_on: true};
        if (path.includes('verify_code.async')) {
          const form = new URLSearchParams(body);
          const params = JSON.parse(form.get('params'));
          const code = params.client_input_params.code;
          if (code !== '003754') { stream.respond({':status': 400}); return stream.end('{}'); }
          const authorization = 'Bearer IGT:2:' + Buffer.from(JSON.stringify({ds_user_id: '123', sessionid: 'synthetic-session'})).toString('base64');
          const raw = {login_response: JSON.stringify({status: 'ok', logged_in_user: {pk: '123'}}), headers: JSON.stringify({'IG-Set-Authorization': authorization}), cookies: ''};
          result = {layout: {bloks_payload: {action: '(action ' + JSON.stringify(JSON.stringify(raw)) + ')'}}};
        }
      }
      stream.respond({':status': 200, 'content-type': 'application/json', ...extra});
      return stream.end(JSON.stringify(result));
    }
    if (path === '/reset') return stream.close(http2.constants.NGHTTP2_INTERNAL_ERROR);
    if (path === '/slow') return setTimeout(() => { if (!stream.destroyed) { stream.respond({':status': 200}); stream.end('{}'); } }, 1500);
    if (path === '/redirect') { stream.respond({':status': 307, location: '/must-not-send'}); return stream.end('{}'); }
    if (path === '/refused') { stream.respond({':status': 401, 'content-type': 'application/json'}); return stream.end(JSON.stringify({message: 'Please wait a few minutes before you try again.'})); }
    if (path === '/limited') { stream.respond({':status': 429, 'content-type': 'text/html', 'retry-after': '60'}); return stream.end('<html>limited</html>'); }
    if (path === '/counts') { stream.respond({':status': 200, 'content-type': 'application/json'}); return stream.end(JSON.stringify(counts)); }
    const response = {path, method: headers[':method'], body, cookie: headers.cookie || '', http_version: '2', tls: stream.session.socket.getProtocol()};
    let payload = Buffer.from(JSON.stringify(response));
    const outgoing = {':status': 200, 'content-type': 'application/json'};
    if (path === '/first') {
      outgoing['set-cookie'] = ['csrftoken=synthetic-csrf; Path=/; Secure; HttpOnly', 'sessionid=synthetic-session; Path=/private; Secure; HttpOnly'];
      outgoing['content-encoding'] = 'gzip';
      payload = zlib.gzipSync(payload);
    }
    stream.respond(outgoing);
    stream.end(payload);
  });
});
const h1 = https.createServer(tls, (_req, res) => { res.end('{}'); });
await new Promise(resolve => h2.listen(0, '127.0.0.1', resolve));
await new Promise(resolve => h1.listen(0, '127.0.0.1', resolve));
process.stdout.write(JSON.stringify({h2: h2.address().port, h1: h1.address().port}) + '\n');
process.on('SIGTERM', () => { h2.close(); h1.close(); process.exit(0); });
