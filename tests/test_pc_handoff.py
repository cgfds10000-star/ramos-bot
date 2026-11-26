"""Fluxo real de dois backends HTTP, com respostas do Instagram simuladas."""
import http.client
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app import RamosService, make_server
from storage import PREFIX
from test_login_flow import WORKER_HARNESS
from cryptography.fernet import Fernet

VALIDATE_HARNESS = r'''
from contextlib import ExitStack
from unittest.mock import patch
from instagrapi import Client
import worker,instagram_login_apk as core
def validate(client, owner):
    assert owner == 'teste' and str(client.user_id) == '123' and client.sessionid
with ExitStack() as stack:
    stack.enter_context(patch.object(core, 'validate_action_session', validate))
    stack.enter_context(patch.object(Client, 'login', side_effect=AssertionError('Cloud cannot perform password login')))
    stack.enter_context(patch.object(Client, 'private_request', side_effect=AssertionError('Unexpected network request')))
    stack.enter_context(patch.object(core, 'get_profile', return_value={'id':'456','username':'alvo'}))
    worker.main()
'''


class PcHandoffChecks(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'RAMOS_HOST':'127.0.0.1','RAMOS_API_KEY':'synthetic-api-key','RAMOS_ADMIN_PASSWORD':'',
                                      'RAMOS_ENCRYPTION_KEY':Fernet.generate_key().decode(),'RAMOS_TRUSTED_ORIGINS':'https://ramos-bot-production.up.railway.app'})
        self.env.start()
        self.pc=RamosService(Path(self.temp.name)/'pc',worker_command=[sys.executable,'-u','-c',WORKER_HARNESS],dependency_check=lambda:True)
        self.cloud=RamosService(Path(self.temp.name)/'cloud',worker_command=[sys.executable,'-u','-c',VALIDATE_HARNESS],dependency_check=lambda:True)
        self.servers=[]
        for service in (self.pc,self.cloud):
            server=make_server(service,0);threading.Thread(target=server.serve_forever,daemon=True).start();self.servers.append(server)

    def tearDown(self):
        for server in self.servers:server.shutdown();server.server_close()
        self.pc.close();self.cloud.close();self.env.stop();self.temp.cleanup()

    def request(self,index,path,body=None,*,origin=None,token=True,method=None):
        with patch.dict(os.environ,{'RAMOS_PC_CONNECTOR':'1' if index==0 else '0','RAMOS_REQUIRE_PC_LOGIN':'0' if index==0 else '1'}):
            connection=http.client.HTTPConnection('127.0.0.1',self.servers[index].server_address[1],timeout=4)
            headers={'Content-Type':'application/json'}
            if token:headers['Authorization']='Bearer synthetic-api-key'
            if origin:headers['Origin']=origin
            if body is not None:headers['X-Ramos-CSRF']=(self.pc if index==0 else self.cloud).csrf
            connection.request(method or ('POST' if body is not None else 'GET'),'/api'+path,body=None if body is None else json.dumps(body),headers=headers)
            response=connection.getresponse();raw=response.read();result=(response.status,dict(response.getheaders()),json.loads(raw) if raw else None);connection.close();return result

    def wait(self,predicate):
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            if predicate():return
            time.sleep(.01)
        self.fail('A operação não terminou.')

    def test_pc_login_and_two_factor_are_transferred_and_cloud_only_reuses_session(self):
        sid=self.pc.data['sessions'][0]['id'];cloud_sid=self.cloud.data['sessions'][0]['id']
        with patch('app.subprocess.Popen',wraps=subprocess.Popen) as spawn:
            self.assertEqual(self.request(1,f'/sessions/{cloud_sid}/login',{'username':'teste','password':'synthetic-password'})[0],409)
            self.assertEqual(spawn.call_count,0)
            self.assertEqual(self.request(0,f'/sessions/{sid}/login',{'username':'teste','password':'synthetic-password'})[0],200)
            self.wait(lambda:self.pc.session(sid).get('challenge'))
            challenge=self.pc.session(sid)['challenge']
            self.assertEqual(self.request(0,f'/sessions/{sid}/input',{'challenge_id':challenge['id'],'code':'003754'})[0],200)
            self.wait(lambda:sid not in self.pc.jobs)
            self.assertTrue(self.pc.session(sid)['authenticated'])
            status,_,exported=self.request(0,f'/connector/session/{sid}')
            self.assertEqual(status,200);self.assertTrue(exported.pop('ready'))
            body={**exported,'session_id':cloud_sid,'import_id':'synthetic-transfer-id-123'}
            status,_,result=self.request(1,'/sessions/import',body)
            self.assertEqual(status,200);self.assertEqual(result['session_id'],cloud_sid)
            self.wait(lambda:cloud_sid not in self.cloud.jobs)
            self.assertTrue(self.cloud.session(cloud_sid)['authenticated'])
            self.assertEqual(spawn.call_count,2)
            self.assertEqual(self.request(1,'/sessions/import',body)[2],result)
            self.assertEqual(spawn.call_count,2)
            self.assertEqual(self.request(0,'/connector/complete',{})[0],200)
            self.servers[0].shutdown();self.servers[0].server_close();self.pc.close()
            self.assertEqual(self.request(1,f'/sessions/{cloud_sid}/command',{'command':'perfil','target':'alvo'})[0],200)
            self.wait(lambda:cloud_sid not in self.cloud.jobs)
            self.assertTrue(self.cloud.session(cloud_sid)['authenticated'])
            self.assertEqual(self.cloud.session(cloud_sid)['last_result']['username'],'alvo')
            self.assertEqual(spawn.call_count,3)
            self.assertEqual(self.request(1,f'/sessions/{cloud_sid}/disconnect',{})[0],200)
            self.assertEqual(self.request(1,f'/sessions/{cloud_sid}/login',{'username':'teste'})[0],200)
            self.wait(lambda:cloud_sid not in self.cloud.jobs)
            self.assertTrue(self.cloud.session(cloud_sid)['authenticated'])
            self.assertEqual(spawn.call_count,4)
        public=json.dumps(self.cloud.snapshot())
        for secret in ('synthetic-password','003754','synthetic-session'):
            self.assertNotIn(secret,public)
        for path in (Path(self.temp.name)/'cloud').rglob('*.json'):
            self.assertTrue(path.read_bytes().startswith(PREFIX))

    def test_export_requires_token_and_approved_origin(self):
        sid=self.pc.data['sessions'][0]['id'];origin='https://ramos-bot-production.up.railway.app'
        self.assertEqual(self.request(0,f'/connector/session/{sid}',token=False,origin=origin)[0],401)
        self.assertEqual(self.request(0,'/state',origin='https://untrusted.example')[0],403)
        status,headers,_=self.request(0,'/state',origin=origin,method='OPTIONS')
        self.assertEqual(status,204);self.assertEqual(headers['Access-Control-Allow-Origin'],origin)
        self.assertIn('Authorization',headers['Access-Control-Allow-Headers'])

    def test_wrong_account_and_unconfirmed_session_cannot_be_imported(self):
        base={'import_id':'synthetic-transfer-id-123','username':'teste','settings':{'_local_login_username':'other','authorization_data':{'ds_user_id':'123','sessionid':'synthetic-session'}}}
        self.assertEqual(self.request(1,'/sessions/import',base)[0],400)
        base['settings']['_local_login_username']='teste';base['settings']['authorization_data']={}
        self.assertEqual(self.request(1,'/sessions/import',base)[0],400)
        self.assertFalse(self.cloud.jobs)


class ConnectorConfigurationChecks(unittest.TestCase):
    def test_local_secrets_are_stable_and_environment_is_loopback_only(self):
        path=Path(__file__).resolve().parents[1]/'connector'/'pc_bridge.py'
        spec=importlib.util.spec_from_file_location('pc_bridge',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);first=module.load_config(root);second=module.load_config(root)
            self.assertEqual(first,second)
            self.assertGreaterEqual(len(first['api_key']),32)
            env=module.local_environment(root,first)
            self.assertEqual(env['RAMOS_HOST'],'127.0.0.1');self.assertEqual(env['RAMOS_PC_CONNECTOR'],'1')
            self.assertEqual(env['RAMOS_REQUIRE_PC_LOGIN'],'0');self.assertEqual(env['RAMOS_INITIAL_PROXY'],'')
            self.assertEqual(env['RAMOS_TRUSTED_ORIGINS'],'https://ramos-bot-production.up.railway.app')
            self.assertFalse(list(root.glob('data-pc/*')))


if __name__=='__main__':unittest.main()
