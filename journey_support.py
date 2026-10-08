"""Ticket follow-up, always scoped to the passenger's course token or an admin."""
import json,re,time
from urllib.parse import parse_qs,urlparse
from journey_experience import owned
REQUEST_SCHEMA='''CREATE TABLE IF NOT EXISTS journey_support_requests(key_hash TEXT PRIMARY KEY,request_id TEXT NOT NULL,body_hash TEXT NOT NULL)'''
SCHEMA='''CREATE TABLE IF NOT EXISTS journey_support_replies(request_id TEXT PRIMARY KEY,reply TEXT NOT NULL,updated_at BIGINT NOT NULL)'''
def handle_get(app,path,db):
 if path=='/api/admin/journey-support':
  user=app.auth()
  if not user or user.get('role')!='admin':app.sendj({'error':'Non autorisé'},401);return True
  with db() as conn:rows=conn.execute('''SELECT s.*,r.reply,r.updated_at FROM support_requests s LEFT JOIN journey_support_replies r ON r.request_id=s.id ORDER BY s.created_at DESC LIMIT 100''').fetchall()
  app.sendj(rows);return True
 match=re.fullmatch(r'/api/rides/([^/]+)/support',path)
 if not match:return False
 token=(parse_qs(urlparse(app.path).query).get('token') or [''])[0]
 with db() as conn:
  if not owned(conn,match[1],token):app.sendj({'error':'Non autorisé'},401);return True
  rows=conn.execute('''SELECT s.id,s.category,s.message,s.status,s.created_at,r.reply,r.updated_at FROM support_requests s LEFT JOIN journey_support_replies r ON r.request_id=s.id WHERE s.ride_id=%s ORDER BY s.created_at DESC LIMIT 20''',(match[1],)).fetchall()
 app.sendj(rows);return True

def handle_post(app,path,data,db,audit=None):
 match=re.fullmatch(r'/api/admin/journey-support/([A-Za-z0-9_-]+)',path)
 if not match:return False
 user=app.auth()
 if not user or user.get('role')!='admin':app.sendj({'error':'Non autorisé'},401);return True
 status=data.get('status');reply=str(data.get('reply') or '').strip()
 if status not in ('open','in_progress','resolved') or not 8<=len(reply)<=2000:app.sendj({'error':'Choisissez un statut et une réponse entre 8 et 2 000 caractères.'},400);return True
 with db() as conn:
  row=conn.execute('SELECT id FROM support_requests WHERE id=%s FOR UPDATE',(match[1],)).fetchone()
  if not row:app.sendj({'error':'Demande introuvable.'},404);return True
  conn.execute('UPDATE support_requests SET status=%s WHERE id=%s',(status,match[1]))
  conn.execute('''INSERT INTO journey_support_replies(request_id,reply,updated_at) VALUES(%s,%s,%s) ON CONFLICT(request_id) DO UPDATE SET reply=EXCLUDED.reply,updated_at=EXCLUDED.updated_at''',(match[1],reply,int(time.time())))
  if audit:audit(conn,'admin',user.get('name',''),'support.reply','support',match[1],{'status':status})
 app.sendj({'ok':True});return True
