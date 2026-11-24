export default {
 async fetch(request, env) {
  const url=new URL(request.url);
  if(url.pathname.startsWith('/api/')) {
   if(!env.RAMOS_API_URL || !env.RAMOS_API_KEY) return Response.json({error:'Servidor em configuração. Atualize em instantes.'},{status:503});
   const origin=request.headers.get('Origin');
   if(origin && origin!==url.origin) return Response.json({error:'Origem não autorizada.'},{status:403});
   if(!['GET','POST','DELETE'].includes(request.method)) return new Response(null,{status:405});
   const headers=new Headers({'Authorization':'Bearer '+env.RAMOS_API_KEY,'Content-Type':'application/json'});
   const csrf=request.headers.get('X-Ramos-CSRF'); if(csrf)headers.set('X-Ramos-CSRF',csrf);
   try {
    const result=await fetch(env.RAMOS_API_URL+url.pathname,{method:request.method,headers,body:request.method==='GET'?undefined:request.body,redirect:'error',signal:AbortSignal.timeout(15000)});
    return new Response(result.body,{status:result.status,headers:{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}});
   } catch {return Response.json({error:'Não foi possível conectar ao servidor. Tente atualizar a conexão.'},{status:502});}
  }
  const asset=assets[url.pathname]||assets['/index.html'];
  const raw=atob(asset.body);const bytes=Uint8Array.from(raw,c=>c.charCodeAt(0));
  return new Response(request.method==='HEAD'?null:bytes,{headers:{'Content-Type':asset.type,'Cache-Control':url.pathname.startsWith('/assets/')?'public,max-age=31536000,immutable':'no-cache','X-Content-Type-Options':'nosniff','Referrer-Policy':'same-origin','Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self'; frame-ancestors 'self' https://chatgpt.com; base-uri 'self'; form-action 'self'"}});
 }
};
