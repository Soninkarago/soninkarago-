"""Durable booking retries. Keys are bearer secrets; only hashes are stored."""
import hashlib,json,re,time

SCHEMA = '''CREATE TABLE IF NOT EXISTS booking_requests(
 key_hash TEXT PRIMARY KEY, payload_hash TEXT NOT NULL,
 response_json TEXT NOT NULL, created_at BIGINT NOT NULL)'''

def identity(payload):
    key=payload.get('request_key')
    if key is None: return None  # Older installed apps remain compatible.
    if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,128}',key):
        raise ValueError('Référence de commande invalide.')
    canonical=json.dumps({k:v for k,v in payload.items() if k!='request_key'},sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False)
    return hashlib.sha256(key.encode()).hexdigest(),hashlib.sha256(canonical.encode()).hexdigest()

def lookup(conn, request):
    if request is None: return None
    row=conn.execute('SELECT payload_hash,response_json FROM booking_requests WHERE key_hash=%s',(request[0],)).fetchone()
    if row is None: return None
    if row['payload_hash']!=request[1]:
        raise ValueError('Cette référence appartient à une autre commande. Reprenez la commande initiale.')
    response=json.loads(row['response_json'])
    response['replayed']=True
    return response

def claim(conn,request,response):
    if request is None: return None
    inserted=conn.execute('''INSERT INTO booking_requests(key_hash,payload_hash,response_json,created_at)
        VALUES(%s,%s,%s,%s) ON CONFLICT(key_hash) DO NOTHING''',
        (request[0],request[1],json.dumps(response),int(time.time())))
    if inserted.rowcount: return None
    # Unique-key conflict waits for the creating transaction to commit in PostgreSQL.
    return lookup(conn,request)

def finish(conn,request,response):
    if request is not None:
        conn.execute('UPDATE booking_requests SET response_json=%s WHERE key_hash=%s AND payload_hash=%s',
                     (json.dumps(response),request[0],request[1]))
