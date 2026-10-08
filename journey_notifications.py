"""Opt-in Expo push, with delivery tickets/receipts and no location in payloads."""
import hashlib,hmac,json,re,secrets,time,os
from urllib.request import Request,urlopen
from journey_experience import owned
import journey_events
SCHEMAS=(
journey_events.SCHEMA,
journey_events.INDEX,
'''CREATE TABLE IF NOT EXISTS journey_push_subscriptions(id TEXT PRIMARY KEY,kind TEXT NOT NULL,owner_id TEXT NOT NULL,push_token TEXT NOT NULL,expires_at BIGINT NOT NULL,last_event TEXT NOT NULL DEFAULT '',revoked BOOLEAN NOT NULL DEFAULT FALSE,UNIQUE(kind,owner_id,push_token))''',
'''CREATE TABLE IF NOT EXISTS journey_push_deliveries(id TEXT PRIMARY KEY,subscription_id TEXT NOT NULL,event TEXT NOT NULL,status TEXT NOT NULL,ticket_id TEXT,created_at BIGINT NOT NULL,receipt_at BIGINT,error TEXT)''',
)
LABELS={'accepted':'Votre chauffeur est en route','arriving':'Votre chauffeur arrive','in_progress':'Votre course a démarré','completed':'Votre course est terminée','cancelled':'Votre course est annulée','payment_failed':'Votre paiement doit être vérifié','deposit_paid':'Acompte confirmé','fully_paid':'Paiement confirmé'}
def handle_post(app,path,data,db):
 if path not in ('/api/journey/notifications','/api/driver/notifications'):return False
 kind='driver' if path.startswith('/api/driver/') else 'ride'
 token=str(data.get('push_token') or '')
 if not re.fullmatch(r'(?:ExponentPushToken|ExpoPushToken)\[[A-Za-z0-9_-]{10,100}\]',token):app.sendj({'error':'Jeton de notification invalide.'},400);return True
 if not app.check_rate('native-push',20,3600):return True
 with db() as conn:
  if kind=='driver':
   user=app.auth()
   if not user or user.get('role')!='driver':app.sendj({'error':'Connexion chauffeur requise.'},401);return True
   owner=user['driver_id'];expires=int(time.time())+7*86400;event=''
  else:
   owner=str(data.get('ride_id') or '')
   ride=owned(conn,owner,data.get('tracking_token'))
   if not ride:app.sendj({'error':'Non autorisé'},401);return True
   expires=int(time.time())+86400;record=journey_events.latest(conn,owner);event='journal:'+record['id'] if record else ride['status']+':'+str(ride.get('payment_status') or '')
  if data.get('enabled') is False:
   conn.execute('UPDATE journey_push_subscriptions SET revoked=TRUE WHERE kind=%s AND owner_id=%s AND push_token=%s',(kind,owner,token))
  elif data.get('enabled') is True:
   if kind=='driver':conn.execute("UPDATE journey_push_subscriptions SET revoked=TRUE WHERE kind='driver' AND push_token=%s AND owner_id<>%s",(token,owner))
   conn.execute('''INSERT INTO journey_push_subscriptions(id,kind,owner_id,push_token,expires_at,last_event) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(kind,owner_id,push_token) DO UPDATE SET expires_at=EXCLUDED.expires_at,revoked=FALSE''',('P-'+secrets.token_hex(16),kind,owner,token,expires,event))
  else:app.sendj({'error':'Choisissez activer ou désactiver.'},400);return True
 app.sendj({'ok':True});return True

def expo_call(endpoint,payload):
 request=Request('https://exp.host/--/api/v2/push/'+endpoint,json.dumps(payload).encode(),headers={'Content-Type':'application/json','Accept':'application/json',**({'Authorization':'Bearer '+os.environ['EXPO_ACCESS_TOKEN']} if os.environ.get('EXPO_ACCESS_TOKEN') else {})},method='POST')
 with urlopen(request,timeout=10) as response:return json.load(response)

def event_for(conn,sub,now):
 if sub['kind']=='ride':
  ride=conn.execute('SELECT status,payment_status FROM rides WHERE id=%s',(sub['owner_id'],)).fetchone()
  if not ride:return None
  state=ride['status'];payment=ride.get('payment_status')
  label=LABELS.get(state)
  if payment=='failed':label='Votre paiement doit être vérifié'+(' · '+label if label else '')
  if payment in ('paid','fully_paid','deposit_paid'):
   label=('Acompte confirmé' if payment=='deposit_paid' else 'Paiement confirmé')+(' · '+label if label else '')
  ttl=60 if state in ('accepted','arriving','in_progress') else 300
  record=journey_events.latest(conn,sub['owner_id'])
  if record:
   # Never deliver an old state after cancellation/completion or a newer payment.
   if record['status']!=state or (record.get('payment_status') or '')!=(payment or '') or record['created_us']<(now-ttl)*1000000:return None
   return ('journal:'+record['id'],label,ttl) if label else None
  return (state+':'+str(payment or ''),label,ttl) if label else None
 driver=conn.execute('SELECT status,online,last_location_at FROM drivers WHERE id=%s',(sub['owner_id'],)).fetchone()
 if not driver or driver['status']!='approved' or not driver['online'] or int(driver.get('last_location_at') or 0)<now-300:return None
 ride=conn.execute("SELECT id,offer_expires_at FROM rides WHERE offered_driver_id=%s AND status='searching' AND offer_expires_at>%s ORDER BY offer_expires_at DESC LIMIT 1",(sub['owner_id'],now)).fetchone()
 if not ride:return None
 return (ride['id']+':'+str(ride['offer_expires_at']),'Une nouvelle course vous est proposée',max(1,int(ride['offer_expires_at'])-now))

def tick(db):
 now=int(time.time())
 with db() as conn:
  subs=conn.execute('SELECT * FROM journey_push_subscriptions WHERE revoked=FALSE AND expires_at>%s ORDER BY expires_at LIMIT 500',(now,)).fetchall()
 for sub in subs:
  with db() as conn:
   event=event_for(conn,sub,int(time.time()))
   if not event or event[0]==sub['last_event']:continue
   event_id=hashlib.sha256((sub['id']+'|'+event[0]).encode()).hexdigest()
   # An uncertain transmission is not blindly resent. The in-app polling remains authoritative.
   changed=conn.execute("INSERT INTO journey_push_deliveries(id,subscription_id,event,status,created_at) VALUES(%s,%s,%s,'sending',%s) ON CONFLICT(id) DO NOTHING",(event_id,sub['id'],event[0],now)).rowcount
   if not changed:continue
   current=conn.execute('SELECT revoked,expires_at FROM journey_push_subscriptions WHERE id=%s',(sub['id'],)).fetchone()
   latest=event_for(conn,sub,int(time.time()))
   if not current or current['revoked'] or current['expires_at']<=int(time.time()) or not latest or latest[0]!=event[0]:
    conn.execute("UPDATE journey_push_deliveries SET status='obsolete' WHERE id=%s",(event_id,));continue
   event=latest
  try:
   result=expo_call('send',{'to':sub['push_token'],'title':'SoninkaraGo','body':event[1],'sound':'default','ttl':event[2],'collapseId':sub['id'],'tag':sub['id'],'data':{'screen':'driver' if sub['kind']=='driver' else 'client'}})
   ticket=result.get('data') or {};status='ticketed' if ticket.get('status')=='ok' and ticket.get('id') else 'failed';error=str((ticket.get('details') or {}).get('error') or '')[:80]
   with db() as conn:
    conn.execute('UPDATE journey_push_deliveries SET status=%s,ticket_id=%s,error=%s WHERE id=%s',(status,ticket.get('id'),error,event_id))
    conn.execute('UPDATE journey_push_subscriptions SET last_event=%s WHERE id=%s',(event[0],sub['id']))
    if error=='DeviceNotRegistered':conn.execute('UPDATE journey_push_subscriptions SET revoked=TRUE WHERE id=%s',(sub['id'],))
  except Exception:
   with db() as conn:conn.execute("UPDATE journey_push_deliveries SET status='uncertain',error='gateway_response_unconfirmed' WHERE id=%s",(event_id,))
 with db() as conn:
  rows=conn.execute("SELECT id,subscription_id,ticket_id,created_at FROM journey_push_deliveries WHERE status='ticketed' AND created_at<%s ORDER BY created_at LIMIT 100",(now-900,)).fetchall()
 if rows:
  receipts=expo_call('getReceipts',{'ids':[r['ticket_id'] for r in rows]}).get('data') or {}
  with db() as conn:
   for row in rows:
    receipt=receipts.get(row['ticket_id'])
    if not receipt:
     if now-row['created_at']>86400:conn.execute("UPDATE journey_push_deliveries SET status='uncertain',error='receipt_missing' WHERE id=%s",(row['id'],))
     continue
    error=str((receipt.get('details') or {}).get('error') or '')[:80]
    conn.execute('UPDATE journey_push_deliveries SET status=%s,receipt_at=%s,error=%s WHERE id=%s',('handed_off' if receipt.get('status')=='ok' else 'failed',now,error,row['id']))
    if error=='DeviceNotRegistered':conn.execute('UPDATE journey_push_subscriptions SET revoked=TRUE WHERE id=%s',(row['subscription_id'],))
 with db() as conn:
  conn.execute('DELETE FROM journey_push_deliveries WHERE created_at<%s',(now-7*86400,))
  conn.execute('DELETE FROM journey_push_subscriptions WHERE expires_at<%s',(now-86400,))
  journey_events.prune(conn,now)

def worker(db):
 while True:
  try:tick(db)
  except Exception:print('Native push worker: delivery unavailable',flush=True)
  time.sleep(3)
