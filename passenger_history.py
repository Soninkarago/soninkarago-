"""Optional pseudonymous history vault. No phone-based identification or paid SMS.
Only hashes are stored. A ride can be attached only with its private tracking key.
Recovery keys are bearer credentials: never return them in recaps or URL queries.
"""
import hashlib,hmac,re,json,time
from journey_experience import owned
SCHEMAS=(
'''CREATE TABLE IF NOT EXISTS passenger_history_accounts(id TEXT PRIMARY KEY,token_hash TEXT NOT NULL UNIQUE,request_hash TEXT NOT NULL UNIQUE,created_at BIGINT NOT NULL,deleted BOOLEAN NOT NULL DEFAULT FALSE)''',
'''CREATE TABLE IF NOT EXISTS passenger_history_links(account_id TEXT NOT NULL,ride_id TEXT NOT NULL,linked_at BIGINT NOT NULL,PRIMARY KEY(account_id,ride_id))''',
)
def digest(value):return hashlib.sha256(value.encode()).hexdigest()
def handle_post(app,path,data,db,secret):
 if path not in ('/api/passenger/history/create','/api/passenger/history/list','/api/passenger/history/link','/api/passenger/history/delete'):return False
 if not isinstance(data,dict):app.sendj({'error':'Requête invalide.'},400);return True
 if not app.check_rate('passenger-history',60,3600):return True
 action=path.rsplit('/',1)[1]
 if action=='create':
  nonce=data.get('request_key')
  if data.get('consent') is not True or not isinstance(nonce,str) or not re.fullmatch(r'[A-Za-z0-9_-]{40,100}',nonce):app.sendj({'error':'Accord explicite et clé de création requis.'},400);return True
  if not secret:app.sendj({'error':'Historique synchronisé indisponible.'},503);return True
  token=hmac.new(secret.encode(),('passenger-history-v1:'+nonce).encode(),hashlib.sha256).hexdigest()
  request_hash=digest('create:'+nonce);account_id='H-'+digest(token)[:32]
  with db() as conn:
   conn.execute('''INSERT INTO passenger_history_accounts(id,token_hash,request_hash,created_at) VALUES(%s,%s,%s,%s) ON CONFLICT(request_hash) DO NOTHING''',(account_id,digest(token),request_hash,int(time.time())))
   row=conn.execute('SELECT * FROM passenger_history_accounts WHERE request_hash=%s FOR UPDATE',(request_hash,)).fetchone()
   if row['deleted']:app.sendj({'error':'Cet historique a été supprimé. Créez-en un nouveau.','code':'history_deleted'},410);return True
   # Rotation of AUTH_SECRET must not silently create a second vault for a retry.
   if not hmac.compare_digest(row['token_hash'],digest(token)):app.sendj({'error':'Reprise de création indisponible. Contactez l’assistance.'},409);return True
  app.sendj({'account_id':row['id'],'recovery_key':token});return True
 token=data.get('recovery_key')
 if not isinstance(token,str) or not re.fullmatch(r'[a-f0-9]{64}',token):app.sendj({'error':'Clé privée invalide.'},401);return True
 with db() as conn:
  account=conn.execute('SELECT * FROM passenger_history_accounts WHERE token_hash=%s FOR UPDATE',(digest(token),)).fetchone()
  if not account:app.sendj({'error':'Clé privée invalide.'},401);return True
  if action=='delete':
   conn.execute('DELETE FROM passenger_history_links WHERE account_id=%s',(account['id'],))
   conn.execute('UPDATE passenger_history_accounts SET deleted=TRUE WHERE id=%s',(account['id'],))
   response={'ok':True,'financial_records_deleted':False}
  elif account['deleted']:app.sendj({'error':'Historique supprimé.','code':'history_deleted'},410);return True
  elif action=='link':
   ride_id=str(data.get('ride_id') or '')
   if not owned(conn,ride_id,data.get('tracking_token')):app.sendj({'error':'Cette course ne vous appartient pas.'},403);return True
   conn.execute('INSERT INTO passenger_history_links(account_id,ride_id,linked_at) VALUES(%s,%s,%s) ON CONFLICT(account_id,ride_id) DO NOTHING',(account['id'],ride_id,int(time.time())))
   response={'ok':True}
  else:
   rows=conn.execute('''SELECT r.* FROM passenger_history_links h JOIN rides r ON r.id=h.ride_id WHERE h.account_id=%s AND r.status IN ('completed','cancelled') ORDER BY h.linked_at DESC,r.id LIMIT 100''',(account['id'],)).fetchall()
   recaps=[]
   for row in rows:
    recap={k:row.get(k) for k in ('id','pickup','destination','fare','payment','payment_status','status','vehicle','created_at')}
    try:
     route=conn.execute('SELECT stops FROM journey_routes WHERE ride_id=%s',(row['id'],)).fetchone()
     stops=route['stops'] if route else []
     if isinstance(stops,str):stops=json.loads(stops)
     recap['stops']=[{'address':str(s.get('address') or '')[:300]} for s in stops if isinstance(s,dict)] if isinstance(stops,list) else []
    except (ValueError,TypeError):recap['stops']=[]
    recaps.append(recap)
   response={'account_id':account['id'],'rides':recaps,'limit':100,'identity_verified':False}
 app.sendj(response);return True
