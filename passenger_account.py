"""Passenger account recovered with a one-time email code, never with a phone alone."""
import hashlib,hmac,re,secrets,time
from journey_experience import owned
SCHEMAS=(
'''CREATE TABLE IF NOT EXISTS passenger_accounts(id TEXT PRIMARY KEY,email TEXT NOT NULL UNIQUE,name TEXT NOT NULL DEFAULT '',created_at BIGINT NOT NULL,deleted BOOLEAN NOT NULL DEFAULT FALSE)''',
'''CREATE TABLE IF NOT EXISTS passenger_account_sessions(token_hash TEXT PRIMARY KEY,account_id TEXT NOT NULL,expires_at BIGINT NOT NULL)''',
'''CREATE TABLE IF NOT EXISTS passenger_account_codes(challenge_hash TEXT PRIMARY KEY,email TEXT NOT NULL,code_hash TEXT NOT NULL,expires_at BIGINT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,delivered BOOLEAN NOT NULL DEFAULT FALSE,used BOOLEAN NOT NULL DEFAULT FALSE)''',
'''CREATE TABLE IF NOT EXISTS passenger_account_courses(account_id TEXT NOT NULL,ride_id TEXT NOT NULL,linked_at BIGINT NOT NULL,PRIMARY KEY(account_id,ride_id))''',
)
def digest(value):return hashlib.sha256(value.encode()).hexdigest()
def code_digest(secret,challenge,code):return hmac.new(secret.encode(),('passenger-code:'+challenge+':'+code).encode(),hashlib.sha256).hexdigest()
def current(conn,token):
 if not isinstance(token,str) or not re.fullmatch(r'[a-f0-9]{64}',token):return None
 return conn.execute('''SELECT a.* FROM passenger_account_sessions s JOIN passenger_accounts a ON a.id=s.account_id WHERE s.token_hash=%s AND s.expires_at>%s AND a.deleted=FALSE''',(digest(token),int(time.time()))).fetchone()
def handle_post(app,path,data,db,secret,valid_email,mail_ready,send_code,shared_rate):
 if not path.startswith('/api/passenger/account/'):return False
 action=path.rsplit('/',1)[1]
 if action not in ('request','verify','me','profile','link','logout','delete'):app.sendj({'error':'Action indisponible.'},404);return True
 if not isinstance(data,dict) or not secret:app.sendj({'error':'Compte passager indisponible.'},503);return True
 if not app.check_rate('passenger-account',60,3600):return True
 now=int(time.time())
 if action=='request':
  email=valid_email(data.get('email'))
  if not email or data.get('consent') is not True:app.sendj({'error':'Indiquez votre e-mail et acceptez les conditions et la confidentialité.'},400);return True
  if not mail_ready():app.sendj({'error':'L’envoi du code est temporairement indisponible. La réservation sans compte reste possible.'},503);return True
  identity=digest(email)
  if not(shared_rate('passenger-mail-minute:'+identity,1,60) and shared_rate('passenger-mail-hour:'+identity,4,3600) and shared_rate('passenger-mail-global',100,3600)):app.sendj({'error':'Attendez avant de demander un nouveau code.'},429);return True
  challenge=secrets.token_urlsafe(32);hashed=digest(challenge);code=f'{secrets.randbelow(1000000):06d}'
  with db() as conn:
   conn.execute('DELETE FROM passenger_account_codes WHERE expires_at<%s',(now-86400,))
   conn.execute('UPDATE passenger_account_codes SET expires_at=0 WHERE email=%s',(email,))
   conn.execute('INSERT INTO passenger_account_codes(challenge_hash,email,code_hash,expires_at) VALUES(%s,%s,%s,%s)',(hashed,email,code_digest(secret,hashed,code),now+600))
  try:
   send_code(email,code)
   with db() as conn:conn.execute('UPDATE passenger_account_codes SET delivered=TRUE WHERE challenge_hash=%s',(hashed,))
  except Exception:
   app.sendj({'error':'L’envoi du code n’a pas été confirmé. Attendez avant de réessayer.'},503);return True
  app.sendj({'challenge':challenge,'expires_in':600,'resend_after':60,'message':'Code demandé. Vérifiez votre messagerie et les courriers indésirables.'},202);return True
 if action=='verify':
  challenge=data.get('challenge');code=data.get('code')
  if not isinstance(challenge,str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}',challenge) or not isinstance(code,str) or not re.fullmatch(r'\d{6}',code):app.sendj({'error':'Saisissez les six chiffres reçus par e-mail.'},400);return True
  hashed=digest(challenge)
  with db() as conn:
   row=conn.execute('SELECT * FROM passenger_account_codes WHERE challenge_hash=%s FOR UPDATE',(hashed,)).fetchone()
   if not row or not row['delivered'] or row['expires_at']<=now or row['attempts']>=5:app.sendj({'error':'Code invalide ou expiré. Demandez un nouveau code.'},400);return True
   conn.execute('UPDATE passenger_account_codes SET attempts=attempts+1 WHERE challenge_hash=%s',(hashed,))
   if not hmac.compare_digest(row['code_hash'],code_digest(secret,hashed,code)):app.sendj({'error':'Code incorrect ou expiré.'},400);return True
   token=hmac.new(secret.encode(),('passenger-session:'+challenge).encode(),hashlib.sha256).hexdigest()
   existing=conn.execute('SELECT * FROM passenger_account_sessions WHERE token_hash=%s',(digest(token),)).fetchone()
   if row['used'] and not existing:app.sendj({'error':'Code déjà utilisé. Demandez un nouveau code.'},409);return True
   account_id='P-'+secrets.token_hex(16)
   conn.execute('INSERT INTO passenger_accounts(id,email,created_at) VALUES(%s,%s,%s) ON CONFLICT(email) DO NOTHING',(account_id,row['email'],now))
   account=conn.execute('SELECT * FROM passenger_accounts WHERE email=%s FOR UPDATE',(row['email'],)).fetchone()
   if account['deleted']:app.sendj({'error':'Compte supprimé. Contactez l’assistance.'},410);return True
   conn.execute('INSERT INTO passenger_account_sessions(token_hash,account_id,expires_at) VALUES(%s,%s,%s) ON CONFLICT(token_hash) DO NOTHING',(digest(token),account['id'],now+30*86400))
   conn.execute('UPDATE passenger_account_codes SET used=TRUE WHERE challenge_hash=%s',(hashed,))
  app.sendj({'session':token,'email':account['email'],'email_verified':True,'phone_verified':False,'name':account['name']});return True
 with db() as conn:
  account=current(conn,data.get('session'))
  if not account:app.sendj({'error':'Reconnectez-vous avec un code envoyé par e-mail.'},401);return True
  if action=='logout':
   conn.execute('DELETE FROM passenger_account_sessions WHERE token_hash=%s',(digest(data['session']),));response={'ok':True}
  elif action=='profile':
   name=data.get('name')
   if not isinstance(name,str) or not 2<=len(name.strip())<=100 or any(c in name for c in '\r\n\x00'):app.sendj({'error':'Indiquez votre nom (2 à 100 caractères).'},400);return True
   conn.execute('UPDATE passenger_accounts SET name=%s WHERE id=%s',(name.strip(),account['id']));response={'ok':True}
  elif action=='link':
   if not owned(conn,str(data.get('ride_id') or ''),data.get('tracking_token')):app.sendj({'error':'La clé privée de la course est requise.'},403);return True
   conn.execute('INSERT INTO passenger_account_courses(account_id,ride_id,linked_at) VALUES(%s,%s,%s) ON CONFLICT(account_id,ride_id) DO NOTHING',(account['id'],data['ride_id'],now));response={'ok':True}
  elif action=='delete':
   active=conn.execute("SELECT r.id FROM passenger_account_courses c JOIN rides r ON r.id=c.ride_id WHERE c.account_id=%s AND r.status NOT IN ('completed','cancelled','payment_failed','payment_canceled') LIMIT 1",(account['id'],)).fetchone()
   if active:app.sendj({'error':'Terminez les courses en attente avant de supprimer votre compte.'},409);return True
   conn.execute('DELETE FROM passenger_account_sessions WHERE account_id=%s',(account['id'],));conn.execute('DELETE FROM passenger_account_courses WHERE account_id=%s',(account['id'],))
   conn.execute("UPDATE passenger_accounts SET deleted=TRUE,name='',email=%s WHERE id=%s",('closed-'+account['id']+'@invalid',account['id']));response={'ok':True,'financial_records_deleted':False}
  else:
   rides=conn.execute('''SELECT r.id,r.pickup,r.destination,r.vehicle,r.fare,r.payment,r.payment_status,r.status,r.created_at,r.tracking_token,r.passenger_payment_url AS payment_url FROM passenger_account_courses c JOIN rides r ON r.id=c.ride_id WHERE c.account_id=%s ORDER BY c.linked_at DESC LIMIT 100''',(account['id'],)).fetchall()
   response={'email':account['email'],'email_verified':True,'phone_verified':False,'name':account['name'],'rides':rides}
 app.sendj(response);return True
