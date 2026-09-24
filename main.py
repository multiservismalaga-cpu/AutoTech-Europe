from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from datetime import datetime, timezone
import json, os, re, sqlite3, threading, urllib.parse, urllib.request, webbrowser
from html.parser import HTMLParser
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

# Estado de sincronización en memoria: la pantalla inicial no depende de
# una lectura de SQLite mientras otro hilo está importando el catálogo.
SYNC_STATE = {"sync":"iniciando","count":0,"error":None,"dataset":None,"updated_at":None}

app = FastAPI(title="AutoTech Europe", version="1.5.0")
app.mount("/static", StaticFiles(directory=APP_DIR), name="static")

def db():
    # No cambiamos journal_mode en cada petición: hacerlo durante una
    # importación puede bloquear las consultas de estado de la interfaz.
    c = sqlite3.connect(DB, timeout=5.0)
    c.execute("PRAGMA busy_timeout=5000")
    c.row_factory = sqlite3.Row
    return c

def init_db():
    c = db()
    c.execute("PRAGMA journal_mode=WAL")
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
    CREATE TABLE IF NOT EXISTS technical_records(
      id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_id TEXT NOT NULL, category TEXT NOT NULL,
      field TEXT NOT NULL, value TEXT, unit TEXT, source_title TEXT, source_url TEXT,
      source_class TEXT, confidence TEXT, applicable_from TEXT, applicable_to TEXT,
      notes TEXT, created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_technical_vehicle_category ON technical_records(vehicle_id,category);
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
        ensure_seed_vehicle()
        updated_at=datetime.now(timezone.utc).isoformat()
        meta_set("dataset_version",DATASET_VERSION)
        meta_set("dataset_updated_at",updated_at)
        SYNC_STATE.update({"sync":"listo","count":len(model_rows)+1,"error":None,"dataset":DATASET_VERSION,"updated_at":updated_at})
        return {"ok":True,"updated":True,"version":DATASET_VERSION,"count":len(model_rows)}
    except Exception as exc:
        SYNC_STATE.update({"sync":"error","error":str(exc)})
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

def ensure_seed_vehicle():
    # Entrada de prueba explícita y trazable para el perfil técnico BMW G20 320d.
    # Se mantiene separada del catálogo VehiclesDB para no fingir que es un registro OEM completo.
    c=db()
    row=c.execute("SELECT 1 FROM vehicles WHERE id=?",("car/bmw/3-series-320d",)).fetchone()
    if not row:
        raw={"id":"car/bmw/3-series-320d","name":"3 Series 320d","body_types":["sedan"],"years":"2018–2020","source":"BMW public press documentation"}
        c.execute("""INSERT OR REPLACE INTO vehicles
          (id,make,model,kind,body_types,years,availability,popularity,sources,raw_json)
          VALUES(?,?,?,?,?,?,?,?,?,?)""",
          ("car/bmw/3-series-320d","BMW","3 Series 320d","car","Sedan","2018–2020","[\"ES\",\"DE\",\"GB\"]","verified-seed",
           "BMW public press documentation",json.dumps(raw,ensure_ascii=False)))
        c.commit()
    c.close()

def seed_verified_bmw_g20_320d():
    # Perfil limitado deliberadamente a datos que hemos podido contrastar en
    # documentación pública de BMW. No se rellenan mantenimiento, pares,
    # distribución o diagnosis sin una fuente específica de variante.
    c=db()
    exists=c.execute("SELECT 1 FROM technical_records WHERE vehicle_id=? LIMIT 1",("car/bmw/3-series-320d",)).fetchone()
    if exists:
        c.close(); return
    now=datetime.now(timezone.utc).isoformat()
    rows=[
      ("technical specifications","Cilindrada","1995","cm³","BMW 3 Series Sedan G20 320d specifications","https://www.press.bmwgroup.com/spain/article/attachment/T0285540ES/415954","FABRICANTE / OEM","CONTRASTADO","2018","2020","Ficha BMW 320d de lanzamiento G20."),
      ("technical specifications","Potencia","190","CV","BMW 3 Series Sedan G20 320d specifications","https://www.press.bmwgroup.com/spain/article/attachment/T0285540ES/415954","FABRICANTE / OEM","CONTRASTADO","2018","2020","140 kW / 190 CV a 4.000 rpm."),
      ("technical specifications","Par máximo","400","Nm","BMW 3 Series Sedan G20 320d specifications","https://www.press.bmwgroup.com/spain/article/attachment/T0285540ES/415954","FABRICANTE / OEM","CONTRASTADO","2018","2020","400 Nm; la documentación BMW indica el rango de régimen."),
      ("technical specifications","Tipo de motor","B47D20O1","", "BMW 3 Series model specifications","https://www.press.bmwgroup.com/united-kingdom/article/attachment/T0285581EN_GB/426446","FABRICANTE / OEM","CONTRASTADO","2019","2020","Código indicado para 320d G20 en esta tabla de variantes."),
      ("lubricants","Capacidad de aceite motor","5.5","L","BMW 3 Series Sedan G20 320d specifications","https://www.press.bmwgroup.com/spain/article/attachment/T0285540ES/415954","FABRICANTE / OEM","CONTRASTADO","2018","2020","Cantidad publicada por BMW para esta ficha; no equivale por sí sola a especificación de aceite."),
      ("technical specifications","Combustible","Diésel","","BMW 3 Series Sedan G20 320d specifications","https://www.press.bmwgroup.com/spain/article/attachment/T0285540ES/415954","FABRICANTE / OEM","CONTRASTADO","2018","2020","Aplicable a la ficha consultada.")
    ]
    for category,field,value,unit,title,url,source_class,confidence,af,at,notes in rows:
        c.execute("""INSERT INTO technical_records
          (vehicle_id,category,field,value,unit,source_title,source_url,source_class,confidence,applicable_from,applicable_to,notes,created_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          ("car/bmw/3-series-320d",category,field,value,unit,title,url,source_class,confidence,af,at,notes,now))
    c.commit(); c.close()

def classify_source(url):
    domain=urllib.parse.urlparse(url).netloc.lower()
    if any(x in domain for x in [".gov","europa.eu","eur-lex.europa.eu"]): return "OFICIAL / ADMINISTRACIÓN"
    if any(x in domain for x in ["volkswagen","audi","seat","skoda","bmw","mercedes","ford","toyota","renault","peugeot","citroen","opel","vauxhall","volvo","hyundai","kia","nissan","honda","jaguar","landrover","porsche","fiat","dacia"]): return "FABRICANTE / OEM"
    if "vehiclesdb.com" in domain: return "BASE ABIERTA"
    return "FUENTE TÉCNICA / WEB"

def search_web(query,limit=8):
    # DuckDuckGo HTML aplica controles anti-bot a clientes que parecen
    # automatizados. Simulamos una navegación real: POST del formulario,
    # Referer y Sec-Fetch-Mode/Dest.
    url="https://html.duckduckgo.com/html/"
    payload=urllib.parse.urlencode({"q":query,"kl":"es-es"}).encode("utf-8")
    headers={
        "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36",
        "Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language":"es-ES,es;q=0.9,en;q=0.7",
        "Content-Type":"application/x-www-form-urlencoded",
        "Referer":"https://html.duckduckgo.com/",
        "Sec-Fetch-Mode":"navigate",
        "Sec-Fetch-Site":"same-origin",
        "Sec-Fetch-Dest":"document",
    }
    req=urllib.request.Request(url,data=payload,headers=headers,method="POST")
    with urllib.request.urlopen(req,timeout=20) as r:
        html=r.read().decode("utf-8","ignore")

    class DDGParser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.results=[]
            self.current=None
            self.in_title=False
            self.in_snippet=False
            self.buf=[]
        def handle_starttag(self,tag,attrs):
            attrs=dict(attrs)
            classes=(attrs.get("class") or "").split()
            if "result__a" in classes and attrs.get("href"):
                self.current={"url":attrs["href"],"title":"","snippet":""}
                self.in_title=True
                self.buf=[]
            elif self.current and "result__snippet" in classes:
                self.in_snippet=True
                self.buf=[]
        def handle_data(self,data):
            if self.current and (self.in_title or self.in_snippet):
                self.buf.append(data)
        def handle_endtag(self,tag):
            if self.current and self.in_title and tag=="a":
                self.current["title"]=" ".join(self.buf).strip()
                self.in_title=False
                self.buf=[]
            elif self.current and self.in_snippet and tag in ("a","div"):
                self.current["snippet"]=" ".join(self.buf).strip()
                self.in_snippet=False
                self.buf=[]
            if self.current and not self.in_title and not self.in_snippet and tag=="div":
                # Los resultados suelen estar contenidos en un div .result.
                # Solo cerramos cuando ya tenemos título y URL.
                if self.current.get("title"):
                    self.results.append(self.current)
                    self.current=None

    parser=DDGParser()
    parser.feed(html)

    # Fallback sencillo por si DDG cambia el contenedor del resultado.
    if not parser.results:
        class LinkParser(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=True)
                self.items=[]
                self.item=None
                self.active=False
                self.buf=[]
            def handle_starttag(self,tag,attrs):
                a=dict(attrs); classes=(a.get("class") or "").split()
                if tag=="a" and "result__a" in classes and a.get("href"):
                    self.item={"url":a["href"],"title":""}; self.active=True; self.buf=[]
            def handle_data(self,data):
                if self.active: self.buf.append(data)
            def handle_endtag(self,tag):
                if self.active and tag=="a":
                    self.item["title"]=" ".join(self.buf).strip()
                    self.items.append(self.item); self.item=None; self.active=False; self.buf=[]
        lp=LinkParser(); lp.feed(html)
        parser.results=lp.items

    out=[]
    for item in parser.results:
        raw_url=urllib.parse.unquote(item["url"])
        parsed=urllib.parse.urlparse(raw_url)
        href=raw_url
        if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
            target=urllib.parse.parse_qs(parsed.query).get("uddg",[])
            if target: href=target[0]
        if not href.startswith(("http://","https://")): continue
        out.append({
            "title":re.sub(r"\\s+"," ",item.get("title","")).strip(),
            "url":href,
            "snippet":re.sub(r"\\s+"," ",item.get("snippet","")).strip()
        })
        if len(out)>=limit: break
    return out

@app.on_event("startup")
def startup():
    init_db()
    seed_verified_bmw_g20_320d()
    ensure_seed_vehicle()
    SYNC_STATE.update({"sync":"comprobando","count":count_vehicles(),"dataset":meta_get("dataset_version")})
    meta_set("dataset_sync","comprobando")
    # Si existe una base antigua o incompleta, se fuerza una sincronización.
    # VehiclesDB 2026.09.1 contiene miles de modelos; 929 registros indican
    # una base local heredada, no el catálogo completo.
    def sync():
        try:
            current_count=SYNC_STATE.get("count",0)
            needs_refresh=meta_get("dataset_version") != DATASET_VERSION or current_count < 5000
            SYNC_STATE.update({"sync":"descargando" if needs_refresh else "listo","error":None})
            meta_set("dataset_sync",SYNC_STATE["sync"])
            result = refresh_database(True) if needs_refresh else refresh_database(False)
            if not result.get("ok"):
                SYNC_STATE.update({"sync":"error","error":result.get("error","Error desconocido")})
                meta_set("dataset_sync","error")
                meta_set("dataset_error",result.get("error","Error desconocido"))
        except Exception as exc:
            meta_set("dataset_sync","error")
            meta_set("dataset_error",str(exc))
    threading.Thread(target=sync,daemon=True).start()

@app.get("/")
def home(): return FileResponse(APP_DIR/"index.html")

@app.get("/api/status")
def status():
    # Durante la importación no consultamos SQLite: este endpoint debe
    # responder aunque exista una transacción de escritura larga.
    s=SYNC_STATE
    return {"version":app.version,"dataset":s.get("dataset") or "Sin descargar","count":s.get("count",0),"updated_at":s.get("updated_at"),"sync":s.get("sync") or "desconocido","error":s.get("error"),"source":"VehiclesDB","license":"CC BY 4.0","warning":"La base local parece incompleta" if s.get("count",0) < 5000 else None}

@app.post("/api/database/refresh")
def refresh(): return refresh_database(True)

@app.get("/api/makes")
def makes():
    c=db()
    rows=c.execute("SELECT make,COUNT(*) AS count FROM vehicles GROUP BY make ORDER BY make COLLATE NOCASE").fetchall()
    c.close()
    return [{"make":r["make"],"count":r["count"]} for r in rows]

@app.get("/api/models")
def models(make:str=Query("",max_length=100), q:str=Query("",max_length=120), limit:int=Query(200,ge=1,le=500)):
    c=db()
    params=[]
    where=[]
    if make.strip():
        where.append("make=?")
        params.append(make.strip())
    if q.strip():
        where.append("(model LIKE ? OR raw_json LIKE ?)")
        needle="%"+q.strip()+"%"
        params.extend([needle,needle])
    sql="SELECT id,make,model,kind,years,body_types FROM vehicles"
    if where:
        sql+=" WHERE "+" AND ".join(where)
    sql+=" ORDER BY make COLLATE NOCASE, model COLLATE NOCASE LIMIT ?"
    params.append(limit)
    rows=c.execute(sql,params).fetchall()
    c.close()
    return [dict(r) for r in rows]

@app.get("/api/engine-search")
def engine_search(q:str=Query(...,min_length=2,max_length=80), limit:int=Query(30,ge=1,le=100)):
    needle=normalize(q)
    c=db()
    rows=c.execute(
        "SELECT id,make,model,kind,years,body_types,raw_json FROM vehicles"
    ).fetchall()
    tech=c.execute(
        "SELECT DISTINCT vehicle_id FROM technical_records WHERE lower(field) LIKE '%motor%' AND lower(value) LIKE ?",
        ("%"+q.lower()+"%",)
    ).fetchall()
    c.close()
    tech_ids={r["vehicle_id"] for r in tech}
    ranked=[]
    for r in rows:
        hay=normalize(" ".join([r["make"] or "",r["model"] or "",r["raw_json"] or ""]))
        if needle in hay or r["id"] in tech_ids:
            score=100 if needle in normalize(r["model"] or "") else 40
            if r["id"] in tech_ids: score+=80
            ranked.append((score,dict(r)))
    ranked.sort(key=lambda x:(-x[0],normalize(x[1]["make"]),normalize(x[1]["model"])))
    return [x[1] for x in ranked[:limit]]

def decode_vin_public(vin):
    url="https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/"+urllib.parse.quote(vin,safe="")+"?format=json"
    req=urllib.request.Request(url,headers={"User-Agent":"AutoTech-Europe/1.5","Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=20) as r:
        payload=json.loads(r.read().decode("utf-8","ignore"))
    results=payload.get("Results") or []
    if not results:
        raise ValueError("El decodificador público no devolvió datos")
    row=results[0]
    fields={
        "Make":"Marca","Model":"Modelo","ModelYear":"Año modelo","Series":"Serie",
        "Trim":"Acabado","VehicleType":"Tipo de vehículo","BodyClass":"Carrocería",
        "EngineModel":"Código/modelo de motor","EngineCylinders":"Cilindros",
        "DisplacementL":"Cilindrada (L)","FuelTypePrimary":"Combustible",
        "TransmissionStyle":"Transmisión","DriveType":"Tracción",
        "PlantCountry":"País de fabricación","PlantCity":"Planta/ciudad",
        "Manufacturer":"Fabricante"
    }
    data=[]
    for key,label in fields.items():
        value=str(row.get(key) or "").strip()
        if value and value.upper() not in {"NOT APPLICABLE","NOT REPORTED","UNKNOWN","0"}:
            data.append({"field":label,"value":value})
    return {
        "ok":True,
        "vin":vin,
        "source":"NHTSA vPIC",
        "source_url":"https://vpic.nhtsa.dot.gov/",
        "confidence":"DECODIFICACIÓN PÚBLICA · NO OEM",
        "wmi":vin[:3],
        "vds":vin[3:9],
        "vis":vin[9:17],
        "year_code":vin[9],
        "results":data,
        "raw_count":len(results)
    }

@app.get("/api/vin/decode/{vin}")
def decode_vin(vin:str):
    raw=vin.strip().upper().replace(" ","").replace("-","")
    if not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}",raw):
        return JSONResponse({"ok":False,"error":"El VIN debe tener 17 caracteres válidos (sin I, O ni Q)."},status_code=400)
    try:
        return decode_vin_public(raw)
    except Exception as exc:
        return {"ok":False,"vin":raw,"source":"NHTSA vPIC","source_url":"https://vpic.nhtsa.dot.gov/","error":f"No se pudo decodificar públicamente este VIN: {exc}","wmi":raw[:3],"vds":raw[3:9],"vis":raw[9:17],"year_code":raw[9],"results":[]}

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
    make=payload.get("make","")
    model=payload.get("model","")
    year=payload.get("year","")
    engine=payload.get("engine","")
    category=payload.get("category","general")
    vehicle_id=payload.get("vehicle_id")
    category_terms={
        "technical specifications":"especificaciones técnicas ficha técnica",
        "maintenance":"mantenimiento intervalos servicio",
        "timing":"distribución correa cadena",
        "torque":"pares de apriete",
        "lubricants":"fluidos aceite lubricantes capacidades",
        "diagnosis":"diagnóstico averías pruebas",
        "drawings":"esquema eléctrico cableado",
        "fuses":"fusibles caja fusibles",
        "oem":"referencias OEM fabricante",
        "repair manuals":"manual reparación procedimiento",
        "engine management":"gestión motor diagnosis",
        "comfort electronics":"electrónica confort carrocería",
        "repair times":"tiempos reparación",
        "recalls":"campañas llamadas a revisión",
        "smart fix":"solución técnica caso",
        "cost estimate":"coste reparación presupuesto",
    }
    term=category_terms.get(category,category)
    query=" ".join(x for x in [make,model,year,engine,term] if x)
    try:
        results=search_web(query)
    except Exception as exc:
        return {"ok":False,"error":f"No se pudo consultar Internet: {exc}","results":[]}
    now=datetime.now(timezone.utc).isoformat()
    c=db()
    for item in results:
        c.execute(
            "INSERT INTO evidence(vehicle_id,query,category,title,url,domain,source_class,confidence,snippet,fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (vehicle_id,query,category,item["title"],item["url"],urllib.parse.urlparse(item["url"]).netloc,
             classify_source(item["url"]),"PENDIENTE DE CONTRASTE",item["snippet"],now)
        )
        item["source_class"]=classify_source(item["url"])
        item["confidence"]="PENDIENTE DE CONTRASTE"
    c.commit()
    c.close()
    return {"ok":True,"query":query,"results":results}

@app.get("/api/technical/{vehicle_id:path}")
def technical(vehicle_id:str, category:str=Query("")):
    c=db()
    if category:
        rows=c.execute("SELECT * FROM technical_records WHERE vehicle_id=? AND category=? ORDER BY id",(vehicle_id,category)).fetchall()
    else:
        rows=c.execute("SELECT * FROM technical_records WHERE vehicle_id=? ORDER BY category,id",(vehicle_id,)).fetchall()

    # El ID del catálogo y el ID técnico pueden ser distintos. Solo usamos
    # el perfil BMW G20 320d cuando el vehículo seleccionado coincide realmente.
    if not rows:
        v=c.execute("SELECT make,model FROM vehicles WHERE id=?",(vehicle_id,)).fetchone()
        if v:
            make=normalize(v["make"])
            model=normalize(v["model"])
            if make=="bmw" and "320d" in model and "3 series" in model:
                fallback_id="car/bmw/3-series-320d"
                if category:
                    rows=c.execute("SELECT * FROM technical_records WHERE vehicle_id=? AND category=? ORDER BY id",(fallback_id,category)).fetchall()
                else:
                    rows=c.execute("SELECT * FROM technical_records WHERE vehicle_id=? ORDER BY category,id",(fallback_id,)).fetchall()
    c.close()
    return [dict(r) for r in rows]

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
