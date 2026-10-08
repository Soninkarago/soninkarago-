"""Passenger-scoped trust features. No driver documents or account tokens are public."""
import json, base64, hashlib, hmac, html, io, re, secrets, time
from PIL import Image, ImageOps, UnidentifiedImageError
SCHEMAS = (
'''CREATE TABLE IF NOT EXISTS journey_routes(ride_id TEXT PRIMARY KEY,route_polyline TEXT NOT NULL,quote_token TEXT NOT NULL,stops TEXT NOT NULL)''',
'''CREATE TABLE IF NOT EXISTS journey_codes(ride_id TEXT PRIMARY KEY,code TEXT NOT NULL,verified_at BIGINT,attempts INTEGER NOT NULL DEFAULT 0,locked_until BIGINT NOT NULL DEFAULT 0)''',
'''CREATE TABLE IF NOT EXISTS journey_shares(token_hash TEXT PRIMARY KEY,ride_id TEXT NOT NULL,expires_at BIGINT NOT NULL,revoked BOOLEAN NOT NULL DEFAULT FALSE)''',
'''CREATE TABLE IF NOT EXISTS journey_ratings(ride_id TEXT PRIMARY KEY,driver_id TEXT NOT NULL,stars INTEGER NOT NULL,comment TEXT NOT NULL,created_at BIGINT NOT NULL)''',
'''CREATE TABLE IF NOT EXISTS driver_profiles(driver_id TEXT PRIMARY KEY,make TEXT NOT NULL,model TEXT NOT NULL,color TEXT NOT NULL,plate TEXT NOT NULL,portrait TEXT NOT NULL,vehicle_photo TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',updated_at BIGINT NOT NULL,reviewed_at BIGINT)''',
)
ACTIVE=('accepted','arriving','in_progress','deposit_paid','fully_paid')
def clean_photo(encoded, max_size=480):
    if not isinstance(encoded,str) or len(encoded)>5600000: raise ValueError('Photo trop volumineuse (4 Mo maximum).')
    try:
        raw=base64.b64decode(encoded,validate=True)
        if len(raw)>4*1024*1024: raise ValueError('Photo trop volumineuse.')
        with Image.open(io.BytesIO(raw)) as source:
            if source.format not in ('JPEG','PNG') or source.width*source.height>20000000: raise ValueError('Choisissez une photo JPEG ou PNG de moins de 20 mégapixels.')
            source.load(); picture=ImageOps.exif_transpose(source).convert('RGB');picture.thumbnail((max_size,max_size))
            output=io.BytesIO();picture.save(output,format='JPEG',quality=80)
        return 'data:image/jpeg;base64,'+base64.b64encode(output.getvalue()).decode()
    except (UnidentifiedImageError,OSError,Image.DecompressionBombError,ValueError) as error:
        raise ValueError('Photo invalide : choisissez un fichier JPEG ou PNG.') from error

def owned(conn,ride_id,token):
    ride=conn.execute('SELECT * FROM rides WHERE id=%s',(ride_id,)).fetchone()
    if not ride or not token or not hmac.compare_digest(str(ride.get('tracking_token') or ''),str(token)): return None
    return ride

def ensure_code(conn,ride_id):
    conn.execute('INSERT INTO journey_codes(ride_id,code) VALUES(%s,%s) ON CONFLICT(ride_id) DO NOTHING',(ride_id,f'{secrets.randbelow(10000):04d}'))
    return conn.execute('SELECT * FROM journey_codes WHERE ride_id=%s',(ride_id,)).fetchone()

def trust_fields(conn,ride):
    code=conn.execute('SELECT * FROM journey_codes WHERE ride_id=%s',(ride['id'],)).fetchone()
    route=conn.execute('SELECT route_polyline,quote_token,stops FROM journey_routes WHERE ride_id=%s',(ride['id'],)).fetchone()
    profile=conn.execute("SELECT p.* FROM driver_profiles p JOIN drivers d ON d.id=p.driver_id WHERE p.driver_id=%s AND p.status='approved' AND p.plate=d.vehicle_plate",(ride.get('driver_id') or '',)).fetchone()
    rating=conn.execute('SELECT AVG(stars) AS average,COUNT(*) AS count FROM journey_ratings WHERE driver_id=%s',(ride.get('driver_id') or '',)).fetchone()
    recovered={**dict(route),'stops':json.loads(route['stops'])} if route else {}
    return {**recovered,'pickup_code':code['code'] if code and ride.get('status') in ('searching','offered','accepted','arriving') else None,
            'pickup_verified':bool(code and code.get('verified_at')),
            'driver_profile':{k:profile[k] for k in ('make','model','color','plate','portrait','vehicle_photo','updated_at')} if profile and ride.get('status') in ACTIVE else None,
            'driver_rating':round(float(rating['average']),1) if rating and rating['average'] is not None else None,
            'driver_rating_count':int(rating['count']) if rating else 0}

def handle_post(app,path,data,db):
    # Return False only for routes this module does not own.
    match=re.fullmatch(r'/api/rides/([^/]+)/(share|revoke-share|rate|start|arrive)',path)
    if path=='/api/driver/profile':
        user=app.auth()
        if not user or user.get('role') not in ('driver','driver_application'):app.sendj({'error':'Connexion chauffeur requise.'},401);return True
        if not app.check_rate('driver-profile',10,3600,user['driver_id']):return True
        fields={k:str(data.get(k) or '').strip() for k in ('make','model','color','plate')}
        if any(not v or len(v)>60 for v in fields.values()):app.sendj({'error':'Indiquez la marque, le modèle, la couleur et la plaque.'},400);return True
        fields['plate']=fields['plate'].upper()
        try:portrait=clean_photo(data.get('portrait'));vehicle=clean_photo(data.get('vehicle_photo'),1280)
        except ValueError as e:app.sendj({'error':str(e)},400);return True
        with db() as conn:
            driver=conn.execute('SELECT vehicle_plate FROM drivers WHERE id=%s FOR UPDATE',(user['driver_id'],)).fetchone()
            normalize=lambda v:re.sub(r'[^A-Z0-9]','',str(v or '').upper())
            if not driver or normalize(driver['vehicle_plate'])!=normalize(fields['plate']):app.sendj({'error':'La plaque doit correspondre à la carte grise de votre dossier.'},409);return True
            fields['plate']=str(driver['vehicle_plate']).upper()
            previous=conn.execute('SELECT updated_at FROM driver_profiles WHERE driver_id=%s',(user['driver_id'],)).fetchone()
            version=max(int(time.time()*1000),int(previous['updated_at'])+1 if previous else 0)
            conn.execute('''INSERT INTO driver_profiles(driver_id,make,model,color,plate,portrait,vehicle_photo,status,updated_at) VALUES(%s,%s,%s,%s,%s,%s,%s,'pending',%s) ON CONFLICT(driver_id) DO UPDATE SET make=EXCLUDED.make,model=EXCLUDED.model,color=EXCLUDED.color,plate=EXCLUDED.plate,portrait=EXCLUDED.portrait,vehicle_photo=EXCLUDED.vehicle_photo,status='pending',updated_at=EXCLUDED.updated_at,reviewed_at=NULL''',(user['driver_id'],fields['make'],fields['model'],fields['color'],fields['plate'],portrait,vehicle,version))
        app.sendj({'ok':True,'status':'pending','message':'Photos et véhicule transmis pour vérification.'});return True
    review=re.fullmatch(r'/api/admin/drivers/([^/]+)/profile-review',path)
    if review:
        user=app.auth()
        if not user or user.get('role')!='admin':app.sendj({'error':'Non autorisé'},401);return True
        status=data.get('status');version=data.get('updated_at')
        if status not in ('approved','rejected') or not isinstance(version,int):app.sendj({'error':'Décision ou version invalide.'},400);return True
        with db() as conn:
            conn.execute('SELECT id FROM drivers WHERE id=%s FOR UPDATE',(review[1],)).fetchone()
            profile=conn.execute('SELECT p.plate,d.vehicle_plate FROM driver_profiles p JOIN drivers d ON d.id=p.driver_id WHERE p.driver_id=%s',(review[1],)).fetchone()
            if not profile or profile['plate']!=profile['vehicle_plate']:app.sendj({'error':'Le véhicule a changé. Demandez de nouvelles photos.'},409);return True
            changed=conn.execute("UPDATE driver_profiles SET status=%s,reviewed_at=%s WHERE driver_id=%s AND updated_at=%s AND status='pending'",(status,int(time.time()),review[1],version)).rowcount
        app.sendj({'ok':bool(changed)},200 if changed else 409);return True
    if not match:return False
    ride_id,action=match.groups()
    if not app.check_rate('journey-'+action,20,3600,ride_id):return True
    with db() as conn:
        if action in ('start','arrive'):
            user=app.auth()
            if not user or user.get('role')!='driver':app.sendj({'error':'Connexion chauffeur requise.'},401);return True
            ride=conn.execute('SELECT * FROM rides WHERE id=%s FOR UPDATE',(ride_id,)).fetchone()
            if not ride or ride.get('driver_id')!=user.get('driver_id'):app.sendj({'error':'Non autorisé'},403);return True
            if action=='arrive':
                if ride.get('status')=='arriving':app.sendj({'ok':True,'already_arrived':True});return True
                if ride.get('status')!='accepted' or ride.get('vehicle')=='Minicar 14 places':app.sendj({'error':'Arrivée indisponible pour cette course.'},409);return True
                conn.execute("UPDATE rides SET status='arriving' WHERE id=%s",(ride_id,));app.sendj({'ok':True});return True
            if ride.get('status')=='in_progress':app.sendj({'ok':True,'already_started':True});return True
            if ride.get('vehicle')=='Minicar 14 places' or ride.get('status') not in ('accepted','arriving'):app.sendj({'error':'Cette course ne peut pas démarrer.'},409);return True
            code=ensure_code(conn,ride_id);now=int(time.time())
            if code['locked_until']>now:app.sendj({'error':'Trop de tentatives. Réessayez dans 10 minutes.'},429);return True
            supplied=str(data.get('code') or '')
            if not re.fullmatch(r'\d{4}',supplied) or not hmac.compare_digest(code['code'],supplied):
                attempts=(code['attempts'] if not code['locked_until'] else 0)+1
                conn.execute('UPDATE journey_codes SET attempts=%s,locked_until=%s WHERE ride_id=%s',(attempts,now+600 if attempts>=5 else 0,ride_id))
                app.sendj({'error':'Code incorrect. Demandez les quatre chiffres au passager.'},400);return True
            conn.execute('UPDATE journey_codes SET verified_at=%s,attempts=0,locked_until=0 WHERE ride_id=%s',(now,ride_id))
            conn.execute("UPDATE rides SET status='in_progress' WHERE id=%s",(ride_id,));app.sendj({'ok':True});return True
        ride=owned(conn,ride_id,data.get('tracking_token'))
        if not ride:app.sendj({'error':'Non autorisé'},401);return True
        if action=='share':
            if ride.get('status') not in ACTIVE+('searching','offered'):app.sendj({'error':'Le partage est disponible pendant une course active.'},409);return True
            token=secrets.token_urlsafe(32);expires=int(time.time())+7200
            conn.execute('UPDATE journey_shares SET revoked=TRUE WHERE ride_id=%s',(ride_id,))
            conn.execute('INSERT INTO journey_shares(token_hash,ride_id,expires_at) VALUES(%s,%s,%s)',(hashlib.sha256(token.encode()).hexdigest(),ride_id,expires))
            app.sendj({'url':'https://soninkarago.sn/journey/'+token,'expires_at':expires});return True
        if action=='revoke-share':
            conn.execute('UPDATE journey_shares SET revoked=TRUE WHERE ride_id=%s',(ride_id,));app.sendj({'ok':True});return True
        stars=data.get('stars');comment=str(data.get('comment') or '').strip()
        if type(stars) is not int or not 1<=stars<=5 or len(comment)>1000:app.sendj({'error':'Choisissez une note de 1 à 5 et un commentaire de 1 000 caractères maximum.'},400);return True
        if ride.get('status')!='completed' or not ride.get('driver_id'):app.sendj({'error':'Vous pourrez noter le chauffeur après la course.'},409);return True
        conn.execute('''INSERT INTO journey_ratings(ride_id,driver_id,stars,comment,created_at) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(ride_id) DO UPDATE SET stars=EXCLUDED.stars,comment=EXCLUDED.comment''',(ride_id,ride['driver_id'],stars,comment,int(time.time())))
        app.sendj({'ok':True});return True

def handle_get(app,path,db):
    if path=='/api/driver/profile' or re.fullmatch(r'/api/admin/drivers/[^/]+/profile',path):
        user=app.auth();admin=path.startswith('/api/admin/')
        if not user or (admin and user.get('role')!='admin') or (not admin and user.get('role') not in ('driver','driver_application')):app.sendj({'error':'Non autorisé'},401);return True
        driver_id=path.split('/')[4] if admin else user['driver_id']
        with db() as conn:profile=conn.execute('SELECT * FROM driver_profiles WHERE driver_id=%s',(driver_id,)).fetchone()
        app.sendj({'profile':profile});return True
    share=re.fullmatch(r'/journey/([A-Za-z0-9_-]{43})',path)
    if not share:return False
    with db() as conn:
        row=conn.execute('''SELECT r.status,r.driver_lat,r.driver_lng,r.driver_location_at,r.driver_name,d.vehicle_plate FROM journey_shares s JOIN rides r ON r.id=s.ride_id LEFT JOIN drivers d ON d.id=r.driver_id WHERE s.token_hash=%s AND s.revoked=FALSE AND s.expires_at>%s''',(hashlib.sha256(share[1].encode()).hexdigest(),int(time.time()))).fetchone()
    if not row or row['status'] not in ACTIVE+('searching','offered'):
        app.sendh('<!doctype html><html lang="fr"><meta name="viewport" content="width=device-width"><title>SoninkaraGo</title><h1>Partage terminé</h1><p>Ce lien a expiré ou a été désactivé.</p></html>',410);return True
    labels={'accepted':'Chauffeur en route','arriving':'Chauffeur à proximité','in_progress':'Course en cours','searching':'Recherche de chauffeur','offered':'Recherche de chauffeur','deposit_paid':'Acompte reçu','fully_paid':'Paiement reçu'}
    fresh=row.get('driver_location_at') and 0<=int(time.time())-int(row['driver_location_at'])<=60
    link=''
    if fresh and row.get('driver_lat') is not None and row.get('driver_lng') is not None:
        link=f'<a href="https://www.google.com/maps?q={float(row["driver_lat"])},{float(row["driver_lng"])}">Voir la position actuelle sur la carte</a>'
    else:link='<p>Position récente indisponible. Actualisez dans quelques instants.</p>'
    app.sendh('<!doctype html><html lang="fr"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><meta http-equiv="refresh" content="15"><title>Suivi SoninkaraGo</title><body style="font:18px system-ui;background:#f4f7f5;color:#123b2c;padding:28px;max-width:640px;margin:auto"><h1>SoninkaraGo</h1><h2>'+html.escape(labels.get(row['status'],'Suivi de course'))+'</h2><p>'+html.escape(str(row.get('driver_name') or 'Attribution en cours'))+'</p><p>Plaque : '+html.escape(str(row.get('vehicle_plate') or 'À confirmer'))+'</p>'+link+'<p><a href="">Actualiser le suivi</a></p><p>Lien temporaire partagé par le passager. Aucun accès à son compte ou à ses paiements.</p></body></html>');return True

def purge_driver(conn,driver_id):
    conn.execute('DELETE FROM driver_profiles WHERE driver_id=%s',(driver_id,))
    conn.execute("DELETE FROM journey_push_subscriptions WHERE kind='driver' AND owner_id=%s",(driver_id,))
    conn.execute('DELETE FROM journey_ratings WHERE driver_id=%s',(driver_id,))
