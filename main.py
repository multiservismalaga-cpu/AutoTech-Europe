from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from datetime import datetime, timezone
import json, os, re, sqlite3, threading, urllib.parse, urllib.request, webbrowser
import uvicorn

APP_DIR = Path(__file__).resolve().parent
if os.name == "nt":
    default_data_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "AutoTech Europe" / "Data"
else:
    default_data_dir = APP_DIR / "data"
DATA_DIR = Path(os.environ.get("AUTOTECH_DATA_DIR", default_data_dir))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB = DATA_DIR / "autotech.db"
VEHICLES_URL = "https://cdn.jsdelivr.net/gh/vehiclesdb/vehiclesdb@v2026.09.1/dist/vehicles.json"
DATASET_VERSION = "VehiclesDB 2026.09.1"

app = FastAPI(title="AutoTech Europe", version="1.3.0")
app.mount("/static", StaticFiles(directory=APP_DIR), name="static")

def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS vehicles(
      id TEXT PRIMARY KEY, make TEXT NOT NULL, model TEXT NOT NULL, kind TEXT,
      body_types TEXT, years TEXT, availability TEXT, popularity TEXT,
      sources TEXT, raw_json TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_vehicle_make ON vehicles(make);
    CREATE INDEX IF NOT EXISTS idx_vehicle_model ON vehicles(model);
    CREATE TABLE IF NOT EXISTS evidence(
      id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_id TEXT, query TEXT, category TEXT,
      title TEXT, url TEXT, domain TEXT, source_class TEXT, confidence TEXT,
      snippet TEXT, fetched_at TEXT
    );
    """)
    c.commit(); c.close()

def meta_get(key):
    c=db(); row=c.execute("SELECT value FROM meta WHERE key=?",(key,)).fetchone(); c.close()
    return row["value"] if row else None

def meta_set(key,value):
    c=db(); c.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,value)); c.commit(); c.close()

def normalize(value):
    return re.sub(r"[^a-z0-9]+"," ",(value or "").lower()).strip()

def parse_dataset(payload):
    rows=[]
    def add(make,obj,kind="car"):
        if isinstance(obj,str): name=obj; raw={"name":name}
        elif isinstance(obj,dict): name=obj.get("name") or obj.get("model"); raw=obj
        else: return
        if not name: return
        vid=raw.get("id") or f"{kind}/{normalize(make).replace(' ','-')}/{normalize(name).replace(' ','-')}"
        rows.append({"id":vid,"make":make,"model":name,"kind":raw.get("kind") or kind,
          "body_types":raw.get("body_types"),"years":raw.get("years"),
          "availability":raw.get("availability"),"popularity":raw.get("popularity"),
          "sources":raw.get("sources"),"raw_json":raw})
    if isinstance(payload,dict):
        for make,models in payload.items():
            if isinstance(models,list):
                for item in models: add(make,item)
            elif isinstance(models,dict):
                for model_name,data in models.items():
                    if isinstance(data,dict):
                        data=dict(data); data.setdefault("name",model_name)
                    else: data={"name":model_name}
                    add(make,data)
    elif isinstance(payload,list):
        for item in payload:
            if isinstance(item,dict):
                add(item.get("make") or item.get("make_name") or item.get("manufacturer") or "Desconocido",item,item.get("kind","car"))
    return rows

def count_vehicles():
    c=db(); n=c.execute("SELECT COUNT(*) n FROM vehicles").fetchone()["n"]; c.close(); return n

def refresh_database(force=True):
    if not force and meta_get("dataset_version"):
        return {"ok":True,"updated":False,"version":meta_get("dataset_version"),"count":count_vehicles()}
    try:
        req=urllib.request.Request(VEHICLES_URL,headers={"User-Agent":"AutoTech-Europe/1.3"})
        with urllib.request.urlopen(req,timeout=45) as r: payload=json.loads(r.read().decode("utf-8"))
        rows=parse_dataset(payload)
        if not rows: raise ValueError("El dataset no contiene registros interpretables")
        c=db(); c.execute("BEGIN"); c.execute("DELETE FROM vehicles")
        for x in rows:
            c.execute("INSERT OR REPLACE INTO vehicles VALUES(?,?,?,?,?,?,?,?,?,?)",
              (x["id"],x["make"],x["model"],x["kind"],json.dumps(x["body_types"],ensure_ascii=False),
               json.dumps(x["years"],ensure_ascii=False),json.dumps(x["availability"],ensure_ascii=False),
               json.dumps(x["popularity"],ensure_ascii=False),json.dumps(x["sources"],ensure_ascii=False),
               json.dumps(x["raw_json"],ensure_ascii=False)))
        c.commit(); c.close()
        meta_set("dataset_version",DATASET_VERSION); meta_set("dataset_updated_at",datetime.now(timezone.utc).isoformat())
        return {"ok":True,"updated":True,"version":DATASET_VERSION,"count":len(rows)}
    except Exception as exc:
        return {"ok":False,"error":str(exc),"version":meta_get("dataset_version"),"count":count_vehicles()}

def classify_source(url):
    domain=urllib.parse.urlparse(url).netloc.lower()
    if any(x in domain for x in [".gov","europa.eu","eur-lex.europa.eu"]): return "OFICIAL / ADMINISTRACIÓN"
    if any(x in domain for x in ["volkswagen","audi","seat","skoda","bmw","mercedes","ford","toyota","renault","peugeot","citroen","opel","vauxhall","volvo","hyundai","kia","nissan","honda","jaguar","landrover","porsche","fiat","dacia"]): return "FABRICANTE / OEM"
    if "vehiclesdb.com" in domain: return "BASE ABIERTA"
    return "FUENTE TÉCNICA / WEB"

def search_web(query,limit=8):
    url="https://html.duckduckgo.com/html/?"+urllib.parse.urlencode({"q":query})
    req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0 AutoTech-Europe"})
    with urllib.request.urlopen(req,timeout=20) as r: html=r.read().decode("utf-8","ignore")
    out=[]
    for m in re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',html,re.S|re.I):
        href=urllib.parse.unquote(m.group(1)); title=re.sub(r"<.*?>","",m.group(2))
        tail=html[m.end():m.end()+1800]; sm=re.search(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',tail,re.S|re.I)
        snippet=re.sub(r"<.*?>"," ",sm.group(1)) if sm else ""
        out.append({"title":re.sub(r"\s+"," ",title).strip(),"url":href,"snippet":re.sub(r"\s+"," ",snippet).strip()})
        if len(out)>=limit: break
    return out

@app.on_event("startup")
def startup():
    init_db()
    # Si existe una base antigua o incompleta, se fuerza una sincronización.
    # VehiclesDB 2026.09.1 contiene miles de modelos; 929 registros indican
    # una base local heredada, no el catálogo completo.
    def sync():
        if meta_get("dataset_version") != DATASET_VERSION or count_vehicles() < 5000:
            refresh_database(True)
        else:
            refresh_database(False)
    threading.Thread(target=sync,daemon=True).start()

@app.get("/")
def home(): return FileResponse(APP_DIR/"index.html")

@app.get("/api/status")
def status():
    return {"version":app.version,"dataset":meta_get("dataset_version") or "Sin descargar","count":count_vehicles(),"updated_at":meta_get("dataset_updated_at"),"source":"VehiclesDB","license":"CC BY 4.0","warning":"La base local parece incompleta" if count_vehicles() < 5000 else None}

@app.post("/api/database/refresh")
def refresh(): return refresh_database(True)

@app.get("/api/vehicles")
def vehicles(q:str=Query("",max_length=120),limit:int=Query(30,ge=1,le=100)):
    c=db()
    try:
        rows=c.execute(
            "SELECT id,make,model,kind,body_types,years,availability,sources FROM vehicles ORDER BY make,model"
        ).fetchall()
    finally:
        c.close()

    if q.strip():
        needle = normalize(q)
        filtered = []
        for row in rows:
            haystack = normalize(f"{row['make']} {row['model']}")
            if needle in haystack:
                filtered.append(row)
                if len(filtered) >= limit:
                    break
        return [dict(r) for r in filtered]

    return [dict(r) for r in rows[:limit]]

@app.get("/api/vehicle/{vehicle_id:path}")
def vehicle(vehicle_id:str):
    c=db(); row=c.execute("SELECT * FROM vehicles WHERE id=?",(vehicle_id,)).fetchone(); c.close()
    if not row: return JSONResponse({"error":"Vehículo no encontrado"},status_code=404)
    return dict(row)

@app.post("/api/research")
def research(payload:dict):
    make=payload.get("make",""); model=payload.get("model",""); year=payload.get("year",""); engine=payload.get("engine",""); category=payload.get("category","general"); vehicle_id=payload.get("vehicle_id")
    query=" ".join(x for x in [make,model,year,engine,category,"technical specifications"] if x)
    try: results=search_web(query)
    except Exception as exc: return {"ok":False,"error":f"No se pudo consultar Internet: {exc}","results":[]}
    now=datetime.now(timezone.utc).isoformat(); c=db()
    for item in results:
        c.execute("INSERT INTO evidence(vehicle_id,query,category,title,url,domain,source_class,confidence,snippet,fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
          (vehicle_id,query,category,item["title"],item["url"],urllib.parse.urlparse(item["url"]).netloc,classify_source(item["url"]),"PENDIENTE DE CONTRASTE",item["snippet"],now))
        item["source_class"]=classify_source(item["url"]); item["confidence"]="PENDIENTE DE CONTRASTE"
    c.commit(); c.close()
    return {"ok":True,"query":query,"results":results}

@app.get("/api/evidence")
def evidence(limit:int=Query(50,ge=1,le=200)):
    c=db(); rows=c.execute("SELECT * FROM evidence ORDER BY id DESC LIMIT ?",(limit,)).fetchall(); c.close(); return [dict(r) for r in rows]

@app.get("/api/license")
def license_info():
    return {"dataset":"VehiclesDB","version":DATASET_VERSION,"license":"CC BY 4.0","attribution":"Vehicle data by VehiclesDB","url":"https://vehiclesdb.com"}

if __name__ == "__main__":
    init_db()
    threading.Timer(1.5, lambda: webbrowser.open("http://127.0.0.1:8000")).start()
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
