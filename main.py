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
VEHICLES_SQLITE_URL = "https://cdn.jsdelivr.net/gh/vehiclesdb/vehiclesdb@v2026.09.1/dist/catalog.sqlite"
DATASET_VERSION = "VehiclesDB 2026.09.1"

app = FastAPI(title="AutoTech Europe", version="1.4.0")
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
    CREATE TABLE IF NOT EXISTS saved_vehicles(
      id INTEGER PRIMARY KEY AUTOINCREMENT, vin TEXT UNIQUE NOT NULL,
      vehicle_id TEXT, created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_saved_vehicle_vin ON saved_vehicles(vin);
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
    tmp = DATA_DIR / "vehiclesdb_catalog.sqlite.download"
    try:
        req=urllib.request.Request(VEHICLES_SQLITE_URL,headers={"User-Agent":"AutoTech-Europe/1.4"})
        with urllib.request.urlopen(req,timeout=180) as r:
            with open(tmp,"wb") as out:
                while True:
                    chunk=r.read(1024*1024)
                    if not chunk: break
                    out.write(chunk)

        src=sqlite3.connect(tmp)
        src.row_factory=sqlite3.Row
        tables={r["name"] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "models" not in tables or "makes" not in tables:
            src.close()
            raise ValueError("El catálogo SQLite descargado no tiene la estructura esperada")

        make_rows={r["id"]:r["name"] for r in src.execute("SELECT id,name FROM makes")}
        model_rows=src.execute(
            "SELECT id,kind,make_id,slug,name,body_types,global_popularity_decile FROM models"
        ).fetchall()

        availability={}
        if "availability" in tables:
            for r in src.execute("SELECT model_id,country FROM availability"):
                availability.setdefault(r["model_id"],[]).append(r["country"])

        src.close()

        if not model_rows:
            raise ValueError("El catálogo SQLite no contiene modelos")

        c=db()
        c.execute("BEGIN")
        c.execute("DELETE FROM vehicles")
        for row in model_rows:
            make=make_rows.get(row["make_id"],"Desconocido")
            body=row["body_types"]
            avail=availability.get(row["id"],[])
            raw={
                "id":row["id"], "kind":row["kind"], "make_id":row["make_id"],
                "slug":row["slug"], "name":row["name"],
                "body_types":body, "global_popularity_decile":row["global_popularity_decile"],
                "availability":avail
            }
            c.execute(
                "INSERT OR REPLACE INTO vehicles VALUES(?,?,?,?,?,?,?,?,?,?)",
                (row["id"],make,row["name"],row["kind"],body,
                 None,json.dumps(avail,ensure_ascii=False),
                 json.dumps(row["global_popularity_decile"],ensure_ascii=False),
                 None,json.dumps(raw,ensure_ascii=False))
            )
        c.commit(); c.close()
        meta_set("dataset_version",DATASET_VERSION)
        meta_set("dataset_updated_at",datetime.now(timezone.utc).isoformat())
        return {"ok":True,"updated":True,"version":DATASET_VERSION,"count":len(model_rows)}
    except Exception as exc:
        try:
            if tmp.exists(): tmp.unlink()
        except Exception:
            pass
        return {"ok":False,"error":str(exc),"version":meta_get("dataset_version"),"count":count_vehicles()}
    finally:
        try:
            if tmp.exists(): tmp.unlink()
        except Exception:
            pass

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
            "SELECT id,make,model,kind,body_types,years,availability,sources,raw_json FROM vehicles"
        ).fetchall()
    finally:
        c.close()

    if not q.strip():
        return [dict(r) for r in sorted(rows,key=lambda x:(normalize(x["make"]),normalize(x["model"])))[:limit]]

    needle=normalize(q)
    tokens=needle.split()

    if len(needle.replace(" ","")) == 17:
        compact=needle.replace(" ","").upper()
        c=db()
        saved=c.execute("SELECT vehicle_id FROM saved_vehicles WHERE vin=?",(compact,)).fetchone()
        c.close()
        if saved and saved["vehicle_id"]:
            match=next((r for r in rows if r["id"]==saved["vehicle_id"]),None)
            if match:
                return [dict(match)]

    ranked=[]
    for row in rows:
        raw=row["raw_json"] or ""
        haystack=normalize(f"{row['make']} {row['model']} {raw}")
        if all(token in haystack for token in tokens):
            score=0
            name=normalize(f"{row['make']} {row['model']}")
            if needle == name: score += 100
            if name.startswith(needle): score += 50
            if needle in name: score += 30
            score += sum(8 for token in tokens if token in name.split())
            score += sum(2 for token in tokens if token in haystack)
            ranked.append((score,row))
    ranked.sort(key=lambda x:(-x[0],normalize(x[1]["make"]),normalize(x[1]["model"])))
    return [dict(row) for _,row in ranked[:limit]]

@app.post("/api/vin")
def save_vin(payload:dict):
    raw=str(payload.get("vin","")).strip().upper().replace(" ","").replace("-","")
    if not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}",raw):
        return JSONResponse({"ok":False,"error":"El VIN debe tener 17 caracteres válidos (sin I, O ni Q)."},status_code=400)
    vehicle_id=payload.get("vehicle_id")
    now=datetime.now(timezone.utc).isoformat()
    c=db()
    try:
        c.execute("""INSERT INTO saved_vehicles(vin,vehicle_id,created_at)
                     VALUES(?,?,?)
                     ON CONFLICT(vin) DO UPDATE SET
                       vehicle_id=excluded.vehicle_id,
                       created_at=excluded.created_at""",(raw,vehicle_id,now))
        c.commit()
    finally:
        c.close()
    return {"ok":True,"vin":raw,"vehicle_id":vehicle_id,"saved_at":now}

@app.get("/api/vin/{vin}")
def get_vin(vin:str):
    raw=vin.strip().upper().replace(" ","").replace("-","")
    c=db()
    row=c.execute("SELECT vin,vehicle_id,created_at FROM saved_vehicles WHERE vin=?",(raw,)).fetchone()
    c.close()
    if not row:
        return JSONResponse({"error":"VIN no encontrado"},status_code=404)
    return dict(row)

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
