from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse
import sqlite3, json, os, time

ROOT=os.path.dirname(os.path.abspath(__file__))
STATIC=os.path.join(ROOT,"static")
DB=os.path.join(ROOT,"soninkarago.db")
PORT=int(os.environ.get("PORT","10000"))

def db():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

def init():
    c=db()
    c.execute('''CREATE TABLE IF NOT EXISTS rides(
      id TEXT PRIMARY KEY, client_name TEXT, phone TEXT, pickup TEXT, destination TEXT,
      vehicle TEXT, payment TEXT, fare INTEGER, fee INTEGER, status TEXT,
      driver_name TEXT, created_at INTEGER)''')
    c.commit(); c.close()

class App(SimpleHTTPRequestHandler):
    def translate_path(self,path):
        p=urlparse(path).path
        if p=="/": p="/index.html"
        return os.path.join(STATIC,p.lstrip("/"))
    def sendj(self,obj,status=200):
        b=json.dumps(obj,ensure_ascii=False).encode()
        self.send_response(status); self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b)
    def body(self):
        n=int(self.headers.get("Content-Length","0"))
        return json.loads(self.rfile.read(n).decode()) if n else {}
    def do_GET(self):
        p=urlparse(self.path).path
        if p=="/api/health": return self.sendj({"ok":True})
        if p=="/api/rides":
            c=db(); rows=c.execute("SELECT * FROM rides ORDER BY created_at DESC").fetchall(); c.close()
            return self.sendj([dict(r) for r in rows])
        if p=="/api/stats":
            c=db(); r=c.execute("SELECT COUNT(*) n,COALESCE(SUM(fare),0) volume,COALESCE(SUM(fee),0) fees FROM rides").fetchone(); c.close()
            return self.sendj(dict(r))
        return super().do_GET()
    def do_POST(self):
        p=urlparse(self.path).path; data=self.body(); c=db()
        if p=="/api/rides":
            pickup=(data.get("pickup") or "").strip(); dest=(data.get("destination") or "").strip()
            if not pickup or not dest:
                c.close(); return self.sendj({"error":"Départ et destination requis"},400)
            vehicle=data.get("vehicle","moto"); fare=int(data.get("fare") or (400 if vehicle=="moto" else 700)); fee=round(fare*.10)
            rid="SG-"+str(int(time.time()*1000))[-8:]
            c.execute("INSERT INTO rides VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(rid,data.get("client_name","Client"),data.get("phone",""),pickup,dest,vehicle,data.get("payment","Espèces"),fare,fee,"searching","",int(time.time())))
            c.commit(); row=c.execute("SELECT * FROM rides WHERE id=?",(rid,)).fetchone(); c.close()
            return self.sendj(dict(row),201)
        if p.startswith("/api/rides/") and p.endswith("/accept"):
            rid=p.split("/")[3]; c.execute("UPDATE rides SET status='accepted',driver_name=? WHERE id=? AND status='searching'",(data.get("driver_name","Chauffeur"),rid)); c.commit(); c.close(); return self.sendj({"ok":True})
        if p.startswith("/api/rides/") and p.endswith("/complete"):
            rid=p.split("/")[3]; c.execute("UPDATE rides SET status='completed' WHERE id=?",(rid,)); c.commit(); c.close(); return self.sendj({"ok":True})
        c.close(); return self.sendj({"error":"Introuvable"},404)

if __name__=="__main__":
    init()
    ThreadingHTTPServer(("0.0.0.0",PORT),App).serve_forever()