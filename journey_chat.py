"""Course-scoped text chat; participants never need each other's phone number."""
import re,secrets,time
from urllib.parse import parse_qs,urlparse
from journey_experience import owned,ACTIVE
SCHEMA='''CREATE TABLE IF NOT EXISTS journey_chat(id TEXT PRIMARY KEY,ride_id TEXT NOT NULL,sender TEXT NOT NULL,message TEXT NOT NULL,created_at BIGINT NOT NULL)'''
def participant(app,conn,ride_id,token):
 if token:
  ride=owned(conn,ride_id,token);role='passenger'
 else:
  user=app.auth()
  if not user or user.get('role')!='driver':return None,None
  ride=conn.execute('SELECT * FROM rides WHERE id=%s',(ride_id,)).fetchone()
  if not ride or ride.get('driver_id')!=user.get('driver_id'):return None,None
  role='driver'
 if not ride or ride.get('status') not in ACTIVE:return None,None
 return ride,role

def handle_get(app,path,db):
 match=re.fullmatch(r'/api/rides/([^/]+)/chat',path)
 if not match:return False
 token=(parse_qs(urlparse(app.path).query).get('token') or [''])[0]
 with db() as conn:
  ride,role=participant(app,conn,match[1],token)
  if not ride:app.sendj({'error':'Le chat est accessible aux participants pendant la course.'},403);return True
  rows=conn.execute('SELECT id,sender,message,created_at FROM journey_chat WHERE ride_id=%s ORDER BY created_at DESC,id DESC LIMIT 100',(match[1],)).fetchall()
 app.sendj(list(reversed(rows)));return True

def handle_post(app,path,data,db):
 match=re.fullmatch(r'/api/rides/([^/]+)/chat',path)
 if not match:return False
 message=str(data.get('message') or '').strip();nonce=str(data.get('request_id') or '')
 if not 1<=len(message)<=1000 or not re.fullmatch(r'[a-zA-Z0-9_-]{16,80}',nonce):app.sendj({'error':'Message ou référence d’envoi invalide.'},400);return True
 if not app.check_rate('trip-chat',30,60,match[1]):return True
 with db() as conn:
  ride,role=participant(app,conn,match[1],data.get('tracking_token'))
  if not ride:app.sendj({'error':'Le chat est accessible aux participants pendant la course.'},403);return True
  import hashlib
  identity=hashlib.sha256((match[1]+'|'+role+'|'+nonce).encode()).hexdigest()
  row=conn.execute('SELECT message FROM journey_chat WHERE id=%s',(identity,)).fetchone()
  if row and row['message']!=message:app.sendj({'error':'Cette référence désigne déjà un autre message.'},409);return True
  conn.execute('INSERT INTO journey_chat(id,ride_id,sender,message,created_at) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING',(identity,match[1],role,message,int(time.time()*1000)))
  stored=conn.execute('SELECT message FROM journey_chat WHERE id=%s',(identity,)).fetchone()
  if stored['message']!=message:app.sendj({'error':'Cette référence désigne déjà un autre message.'},409);return True
 app.sendj({'ok':True});return True
