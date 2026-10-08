"""Transactional ride event journal. No push receipt is treated as phone delivery."""
import re,time
from urllib.parse import parse_qs,urlparse
from journey_experience import owned

SCHEMA='''CREATE TABLE IF NOT EXISTS journey_events(
 id TEXT PRIMARY KEY,ride_id TEXT NOT NULL,status TEXT NOT NULL,payment_status TEXT,
 offered_driver_id TEXT,offer_expires_at BIGINT,created_us BIGINT NOT NULL)'''
INDEX='CREATE INDEX IF NOT EXISTS idx_journey_events_ride ON journey_events(ride_id,created_us DESC,id DESC)'
POSTGRES_FUNCTION='''CREATE OR REPLACE FUNCTION record_journey_event() RETURNS trigger AS $$
BEGIN
 IF TG_OP='UPDATE' THEN
  IF NEW.status IS NOT DISTINCT FROM OLD.status
   AND NEW.payment_status IS NOT DISTINCT FROM OLD.payment_status
   AND NEW.offered_driver_id IS NOT DISTINCT FROM OLD.offered_driver_id
   AND NEW.offer_expires_at IS NOT DISTINCT FROM OLD.offer_expires_at THEN RETURN NEW; END IF;
 END IF;
 INSERT INTO journey_events(id,ride_id,status,payment_status,offered_driver_id,offer_expires_at,created_us)
 VALUES(md5(random()::text || clock_timestamp()::text),NEW.id,NEW.status,NEW.payment_status,
 NEW.offered_driver_id,NEW.offer_expires_at,(extract(epoch FROM clock_timestamp())*1000000)::bigint);
 RETURN NEW;
END; $$ LANGUAGE plpgsql'''
def install(conn):
 conn.execute(SCHEMA);conn.execute(INDEX);conn.execute(POSTGRES_FUNCTION)
 # Installed only after all legacy ride columns have been migrated.
 conn.execute('DROP TRIGGER IF EXISTS ride_journey_event ON rides')
 conn.execute('CREATE TRIGGER ride_journey_event AFTER INSERT OR UPDATE ON rides FOR EACH ROW EXECUTE FUNCTION record_journey_event()')

def latest(conn,ride_id):
 return conn.execute('SELECT * FROM journey_events WHERE ride_id=%s ORDER BY created_us DESC,id DESC LIMIT 1',(ride_id,)).fetchone()

def handle_get(app,path,db):
 match=re.fullmatch(r'/api/rides/([A-Za-z0-9_-]{1,100})/events',path)
 if not match:return False
 token=(parse_qs(urlparse(app.path).query).get('token') or [''])[0]
 with db() as conn:
  if token:ride=owned(conn,match[1],token)
  else:
   user=app.auth();ride=conn.execute('SELECT * FROM rides WHERE id=%s',(match[1],)).fetchone()
   if not user or user.get('role')!='driver' or not ride or ride.get('driver_id')!=user.get('driver_id'):ride=None
  if not ride:app.sendj({'error':'Accès réservé aux participants de la course.'},403);return True
  rows=conn.execute('SELECT id,status,payment_status,created_us FROM journey_events WHERE ride_id=%s ORDER BY created_us DESC,id DESC LIMIT 100',(match[1],)).fetchall()
 app.sendj(list(reversed(rows)));return True

def prune(conn,now=None):
 # Retain recent events and every event for an active/uncertain ride.
 cutoff=(int(now or time.time())-30*86400)*1000000
 conn.execute("DELETE FROM journey_events WHERE created_us<%s AND ride_id NOT IN (SELECT id FROM rides WHERE status NOT IN ('completed','cancelled'))",(cutoff,))
