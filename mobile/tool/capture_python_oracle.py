# Execute the supplied wrapper and its real pinned dependency against an in-memory server.
import importlib.util,json,types,argparse
from pathlib import Path
from unittest.mock import patch
from requests import Response
from requests.structures import CaseInsensitiveDict
from instagrapi.exceptions import TwoFactorRequired
from instagrapi.utils.serialization import dumps
parser=argparse.ArgumentParser(description='Capture offline requests from the supplied Python login wrapper; requires instagrapi==3.2.19.')
parser.add_argument('script',type=Path)
args=parser.parse_args()
spec=importlib.util.spec_from_file_location('provided_login',args.script);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
Base=m.load_client_class()
class Oracle(Base):
 def __init__(self,branch):
  super().__init__(override_app_version=True,session_retry_total=0,public_request_retries_count=0)
  self.capture=[];self.branch=branch;self.uuid='uuid';self.phone_id='phone';self.android_device_id='android-test';self.mid='';self.request_timeout=0
  self.set_locale('pt_BR');self.set_country('BR');self.set_timezone_offset(-10800)
 def private_graphql_www_request(self,friendly_name,variables,**kwargs):
  self.capture.append({'kind':'graphql','variables':variables})
  return {'data':{'usdid_registration':{'success':True}}}
 def password_encrypt(self,password):
  self.password_publickeys()
  return '#PWD_INSTAGRAM:4:0:encrypted'
 def login_flow(self):return True
 def _send_private_request(self,endpoint,*args,data=None,headers=None,**kwargs):
  if args:data=args[0]
  h=self.private_headers(headers)
  domain=kwargs.get('domain') or 'i.instagram.com'
  if kwargs.get('domain'):h['Host']=domain
  self.capture.append({'host':domain,'route':endpoint,'data':data,'signed':kwargs.get('with_signature',True),'headers':h})
  if 'process_client_data' in endpoint:
   result={'layout':{'bloks_payload':{'data':[{'data':{'key':'CAA_ACCOUNT_ACCESS_CONTEXT:aac','initial':'server-aac'}}]}}}
  elif 'attestation' in endpoint:result={'challenge_nonce':'nonce','key_nonce':'key-nonce'}
  elif 'send_login_request' in endpoint:
   result={'two_step_verification_context':'ctx','two_factor_info':{'sms_two_factor_on':self.branch=='sms','totp_two_factor_on':self.branch!='sms'}}
  elif 'verify_code' in endpoint:
   import base64
   auth='Bearer IGT:2:'+base64.b64encode(dumps({'ds_user_id':'123','sessionid':'test-session'}).encode()).decode()
   raw={'login_response':dumps({'status':'ok','logged_in_user':{'pk':'123'}}),'headers':dumps({'IG-Set-Authorization':auth}),'cookies':''}
   result={'layout':{'bloks_payload':{'action':'(action '+json.dumps(dumps(raw))+')'}}}
  else:result={'status':'ok'}
  response=Response();response.status_code=200;response.headers=CaseInsensitiveDict({'ig-set-password-encryption-pub-key':'key','ig-set-password-encryption-key-id':'1'} if 'launcher' in endpoint else {});response._content=dumps(result).encode();self.last_response=response;self.last_json=result
  return result
for branch in ('auto','sms','backup','autenticador'):
 c=Oracle(branch);c.two_factor_method='autenticador' if branch=='autenticador' else 'auto'
 code='12345678' if branch=='backup' else '123456'
 with patch('builtins.input',return_value=code),patch('getpass.getpass',return_value=code),patch('time.sleep'),patch('time.time',return_value=1234567890.123):
  try: accepted=c.login('test','sensitive-password')
  except TwoFactorRequired as error: accepted=m.continue_two_factor(c,error)
 assert accepted
 # Signature tokens are generated for this synthetic device only; omit them from the oracle.
 for call in c.capture:
  if call.get('kind')=='graphql':call['variables']['input']['usdid_token']['sensitive_string_value']='<device-registration>'
  if 'headers' in call:
   call['headers'].pop('X-Meta-Usdid',None)
 (Path(__file__).parent/'fixtures'/('python_'+branch+'.json')).write_text(json.dumps(c.capture,indent=2)+'\n')
 print(branch,len(c.capture),'calls captured offline')
