"""Cancel before pickup; return commission once; reconcile refunds separately.
The PayTech POST is performed once. An uncertain response requires human review.
"""
import hashlib,hmac,json,re,time
from urllib.parse import urlencode
from urllib.request import Request,urlopen
SCHEMAS=(
'''CREATE TABLE IF NOT EXISTS ride_refunds(ride_id TEXT PRIMARY KEY,amount BIGINT NOT NULL,status TEXT NOT NULL,created_at BIGINT NOT NULL,updated_at BIGINT NOT NULL,token_hash TEXT NOT NULL DEFAULT '',error TEXT NOT NULL DEFAULT '')''',
)
LABELS={'queued':'Remboursement à transmettre','submitting':'Remboursement en cours de transmission','requested':'Remboursement demandé au prestataire','confirmed':'Remboursement confirmé','manual_review':'Remboursement à vérifier par l’assistance','awaiting_payment':'Paiement à vérifier après annulation','not_required':'Aucun remboursement demandé'}
def digest(value):return hashlib.sha256(str(value).encode()).hexdigest()
def eligible(ride,code):
 status=ride.get('status')
 if status in ('searching','offered','awaiting_payment','payment_failed','payment_canceled'):return True
 return status in ('accepted','arriving','deposit_paid','fully_paid') and bool(code) and not code.get('verified_at')
def fields(conn,ride_id):
 row=conn.execute('SELECT amount,status,updated_at FROM ride_refunds WHERE ride_id=%s',(ride_id,)).fetchone()
 return {'refund':{**row,'label':LABELS.get(row['status'],'État à vérifier')}} if row else {'refund':None}
def preview(conn,ride):
 code=conn.execute('SELECT verified_at FROM journey_codes WHERE ride_id=%s',(ride['id'],)).fetchone()
 allowed=eligible(ride,code)
 if not allowed:return {'automatic':False,'fee':None,'currency':'XOF','message':'La prise en charge a commencé ou cette course est terminée. Contactez l’assistance pour toute réclamation.'}
 paid=ride.get('payment_status') not in ('unpaid','failed',None,'')
 message='Annulation sans frais supplémentaires avant la prise en charge.'
 if paid and ride.get('payment') in ('Wave','Orange Money'):message+=' Le remboursement du paiement sera demandé ; il ne sera confirmé qu’après retour du prestataire.'
 elif paid:message+=' Pour des espèces déjà remises, le retour de l’argent doit être vérifié avec l’assistance.'
 elif ride.get('status')=='awaiting_payment':message+=' Un paiement confirmé plus tard sera traité séparément. Ne terminez pas le paiement après cette annulation.'
 return {'automatic':True,'fee':0,'currency':'XOF','message':message}
def record_payment(conn,ride,amount,token):
 # Called only after the existing signed sale_complete validation.
 if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,200}',token):token=''
 if ride.get('status') in ('cancelled','canceled'):
  conn.execute("INSERT INTO ride_refunds(ride_id,amount,status,created_at,updated_at,token_hash) VALUES(%s,%s,'queued',%s,%s,%s) ON CONFLICT(ride_id) DO UPDATE SET amount=EXCLUDED.amount,token_hash=EXCLUDED.token_hash,status=CASE WHEN ride_refunds.status IN ('awaiting_payment','not_required') THEN 'queued' ELSE ride_refunds.status END,updated_at=EXCLUDED.updated_at",(ride['id'],amount,int(time.time()),int(time.time()),digest(token) if token else ''))
  conn.execute('UPDATE rides SET payment_status=%s WHERE id=%s',('deposit_paid' if int(ride.get('deposit_amount') or 0) else 'fully_paid',ride['id']))
 else:
  # Keep the provider transaction identity privately for a future refund callback.
  conn.execute("INSERT INTO ride_refunds(ride_id,amount,status,created_at,updated_at,token_hash) VALUES(%s,%s,'not_required',%s,%s,%s) ON CONFLICT(ride_id) DO NOTHING",(ride['id'],amount,int(time.time()),int(time.time()),digest(token) if token else ''))
def cancel(conn,ride,reason):
 if ride.get('status') in ('cancelled','canceled'):return {'ok':True,'status':'cancelled',**fields(conn,ride['id'])}
 code=conn.execute('SELECT verified_at FROM journey_codes WHERE ride_id=%s',(ride['id'],)).fetchone()
 if not eligible(ride,code):raise ValueError('La prise en charge a commencé. Contactez l’assistance pour annuler.')
 now=int(time.time());paid_status=ride.get('payment_status') or 'unpaid'
 paid_amount=int(ride.get('fare') or 0) if paid_status in ('fully_paid','paid') else int(ride.get('deposit_amount') or 0) if paid_status=='deposit_paid' else 0
 if ride.get('commission_charged') and ride.get('driver_id'):
  driver=conn.execute('SELECT id FROM drivers WHERE id=%s FOR UPDATE',(ride['driver_id'],)).fetchone()
  if not driver:raise ValueError('Le solde du chauffeur doit être vérifié par l’assistance avant annulation.')
  conn.execute('UPDATE drivers SET balance=balance+%s WHERE id=%s',(int(ride.get('fee') or 0),ride['driver_id']))
 gateway=conn.execute('SELECT token_hash FROM ride_refunds WHERE ride_id=%s',(ride['id'],)).fetchone()
 online=ride.get('payment') in ('Wave','Orange Money')
 status='queued' if paid_amount and online and gateway else 'manual_review' if paid_amount else 'awaiting_payment' if online and ride.get('status')=='awaiting_payment' else 'not_required'
 conn.execute('INSERT INTO ride_refunds(ride_id,amount,status,created_at,updated_at) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(ride_id) DO UPDATE SET amount=EXCLUDED.amount,status=EXCLUDED.status,updated_at=EXCLUDED.updated_at',(ride['id'],paid_amount,status,now,now))
 conn.execute("UPDATE rides SET status='cancelled',cancelled_at=%s,cancel_reason=%s,commission_charged=FALSE,offered_driver_id=NULL,offer_expires_at=NULL WHERE id=%s",(now,reason,ride['id']))
 return {'ok':True,'status':'cancelled',**fields(conn,ride['id'])}
def request_refund(reference,key,secret):
 request=Request('https://paytech.sn/api/payment/refund-payment',data=urlencode({'ref_command':reference}).encode(),headers={'API_KEY':key,'API_SECRET':secret,'Content-Type':'application/x-www-form-urlencoded'},method='POST')
 with urlopen(request,timeout=20) as response:result=json.loads(response.read(65536))
 return isinstance(result,dict) and result.get('success')==1
def tick(db,key,secret):
 if not key or not secret:
  with db() as conn:conn.execute("UPDATE ride_refunds SET status='manual_review',error='provider_unavailable',updated_at=%s WHERE status='queued'",(int(time.time()),))
  return
 now=int(time.time())
 with db() as conn:
  conn.execute("UPDATE ride_refunds SET status='manual_review',error='response_uncertain',updated_at=%s WHERE status='submitting' AND updated_at<%s",(now,now-120))
  conn.execute("UPDATE ride_refunds SET status='manual_review',error='confirmation_missing',updated_at=%s WHERE status='requested' AND updated_at<%s",(now,now-86400))
  row=conn.execute("SELECT ride_id FROM ride_refunds WHERE status='queued' ORDER BY created_at LIMIT 1 FOR UPDATE").fetchone()
  if not row:return
  claimed=conn.execute("UPDATE ride_refunds SET status='submitting',updated_at=%s WHERE ride_id=%s AND status='queued'",(now,row['ride_id'])).rowcount
  if not claimed:return
 try:accepted=request_refund(row['ride_id'],key,secret)
 except Exception:accepted=False
 with db() as conn:
  conn.execute("UPDATE ride_refunds SET status=%s,error=%s,updated_at=%s WHERE ride_id=%s AND status='submitting'",('requested' if accepted else 'manual_review','' if accepted else 'provider_response_unconfirmed',int(time.time()),row['ride_id']))
def worker(db,key,secret):
 while True:
  try:tick(db,key,secret)
  except Exception:print('Refund worker unavailable',flush=True)
  time.sleep(3)
def handle_notification(app,data,db,key,secret,amount_parser,env):
 if not key or not secret:app.sendj({'error':'Prestataire indisponible.'},503);return
 reference=str(data.get('ref_command') or '');token=str(data.get('token') or '')
 # Refund notifications in PayTech's documentation provide SHA256 credentials.
 authenticated=all(isinstance(data.get(field),str) and hmac.compare_digest(data[field].lower(),digest(value)) for field,value in [('api_key_sha256',key),('api_secret_sha256',secret)])
 if not authenticated or str(data.get('env') or '').lower()!=env or str(data.get('currency') or '').upper()!='XOF' or data.get('type_event')!='refund_complete':app.sendj({'error':'Notification invalide.'},403);return
 try:amount=amount_parser(data.get('item_price'))
 except (ValueError,TypeError):app.sendj({'error':'Montant invalide.'},400);return
 with db() as conn:
  row=conn.execute('SELECT * FROM ride_refunds WHERE ride_id=%s FOR UPDATE',(reference,)).fetchone()
  if not row or row['status'] not in ('submitting','requested','confirmed','manual_review') or amount!=int(row['amount']) or not token or not row['token_hash'] or not hmac.compare_digest(row['token_hash'],digest(token)):
   app.sendj({'error':'Remboursement non rapproché. Vérification requise.'},409);return
  conn.execute("UPDATE ride_refunds SET status='confirmed',error='',updated_at=%s WHERE ride_id=%s",(int(time.time()),reference))
 app.sendj({'ok':True})
