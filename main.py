from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from datetime import datetime, timezone
import json, os, re, sqlite3, threading, urllib.parse, urllib.request, webbrowser
from datetime import timedelta
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
# Hyundai VIN crosscheck enabled

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
    
    CREATE TABLE IF NOT EXISTS source_documents(
      document_id INTEGER PRIMARY KEY AUTOINCREMENT,
      variant_id TEXT,
      title TEXT NOT NULL,
      url TEXT NOT NULL,
      source_class TEXT,
      publisher TEXT,
      document_type TEXT,
      language TEXT,
      revision TEXT,
      retrieved_at TEXT,
      notes TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_source_documents_variant
      ON source_documents(variant_id);

    CREATE TABLE IF NOT EXISTS vehicle_variants(
      variant_id TEXT PRIMARY KEY,
      make TEXT NOT NULL,
      model TEXT NOT NULL,
      generation TEXT,
      engine_family TEXT,
      engine_code TEXT,
      transmission TEXT,
      drive TEXT,
      fuel TEXT,
      market TEXT,
      year_from TEXT,
      year_to TEXT,
      variant_key TEXT NOT NULL,
      source_class TEXT,
      confidence TEXT,
      source_url TEXT,
      notes TEXT,
      created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_vehicle_variants_lookup
      ON vehicle_variants(make,model,engine_code,transmission,drive,market);
    CREATE TABLE IF NOT EXISTS vehicle_variant_map(
      vehicle_id TEXT PRIMARY KEY,
      variant_id TEXT NOT NULL,
      match_method TEXT NOT NULL,
      confidence TEXT NOT NULL,
      created_at TEXT NOT NULL,
      FOREIGN KEY(variant_id) REFERENCES vehicle_variants(variant_id)
    );
    CREATE INDEX IF NOT EXISTS idx_vehicle_variant_map_variant
      ON vehicle_variant_map(variant_id);
    """)
    evidence_cols={r["name"] for r in c.execute("PRAGMA table_info(evidence)").fetchall()}
    if "variant_id" not in evidence_cols:
        c.execute("ALTER TABLE evidence ADD COLUMN variant_id TEXT")
    if "document_id" not in evidence_cols:
        c.execute("ALTER TABLE evidence ADD COLUMN document_id INTEGER")
    if "document_section" not in evidence_cols:
        c.execute("ALTER TABLE evidence ADD COLUMN document_section TEXT")
    if "applicability" not in evidence_cols:
        c.execute("ALTER TABLE evidence ADD COLUMN applicability TEXT")
    c.execute("CREATE INDEX IF NOT EXISTS idx_evidence_variant_category ON evidence(variant_id,category,query)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_evidence_document ON evidence(document_id)")
    
    # Migración incremental de registros técnicos: variant_id es la referencia
    # canónica; vehicle_id se conserva para compatibilidad con bases existentes.
    cols={r["name"] for r in c.execute("PRAGMA table_info(technical_records)").fetchall()}
    if "variant_id" not in cols:
        c.execute("ALTER TABLE technical_records ADD COLUMN variant_id TEXT")
    c.execute("CREATE INDEX IF NOT EXISTS idx_technical_variant_category ON technical_records(variant_id,category)")
    c.commit(); c.close()

def meta_get(key):
    c=db(); row=c.execute("SELECT value FROM meta WHERE key=?",(key,)).fetchone(); c.close()
    return row["value"] if row else None

def meta_set(key,value):
    c=db(); c.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,value)); c.commit(); c.close()

def normalize(value):
    return re.sub(r"[^a-z0-9]+"," ",(value or "").lower()).strip()

def variant_key(make="", model="", year="", engine="", transmission="", drive="", market=""):
    """Normaliza señales de variante para agrupar vehículos técnicamente compatibles."""
    parts=[normalize(make),normalize(model),normalize(engine),normalize(transmission),normalize(drive),normalize(market)]
    y=str(year or "").strip()
    return "|".join(parts+[y])


def ensure_seed_variants():
    """Registra perfiles técnicos canónicos y los relaciona con vehículos concretos."""
    now=datetime.now(timezone.utc).isoformat()
    variants=[
      {
        "variant_id":"car/bmw/3-series-320d","make":"BMW","model":"3 Series 320d",
        "generation":"G20","engine_family":"2.0 Diesel","engine_code":"B47D20O1",
        "transmission":"Automática","drive":"RWD","fuel":"Diésel","market":"ES/EU",
        "year_from":"2018","year_to":"2020","source_class":"FABRICANTE / OEM",
        "confidence":"CONTRASTADO","source_url":"https://www.press.bmwgroup.com/spain/",
        "notes":"Perfil técnico limitado a los registros BMW actualmente contrastados."
      },
      {
        "variant_id":"car/hyundai/kona-sx2-hev-2025","make":"Hyundai","model":"KONA SX2 HEV",
        "generation":"SX2","engine_family":"1.6 GDi HEV","engine_code":"G4LL",
        "transmission":"DCT 6","drive":"FWD","fuel":"Gasolina híbrido","market":"ES/EU",
        "year_from":"2025","year_to":"2025","source_class":"FABRICANTE / OEM",
        "confidence":"CONTRASTADO · EUROPA","source_url":"https://ownersmanual.hyundai.com/",
        "notes":"Perfil técnico canónico del KONA SX2 HEV; el cruce G4LL conserva su alcance declarado."
      },
      {
        "variant_id":"car/kia/niro-sg2-hev-2024","make":"Kia","model":"Niro SG2 HEV",
        "generation":"SG2","engine_family":"Smartstream 1.6 GDi HEV","engine_code":"G4LL",
        "transmission":"DCT 6","drive":"FWD","fuel":"Gasolina híbrido","market":"ES/EU",
        "year_from":"2024","year_to":"2026","source_class":"FABRICANTE / OEM + FUENTE PÚBLICA",
        "confidence":"CONTRASTADO · EUROPA","source_url":"https://ownersmanual.kia.com/full_webhelp/SG2/2024/es_ES/topics/chapter9_4.html",
        "notes":"G4LL procede de homologación pública y no se presenta como confirmación OEM."
      }
    ]
    c=db()
    for v in variants:
        key=variant_key(v["make"],v["model"],v["year_from"],v["engine_code"],v["transmission"],v["drive"],v["market"])
        c.execute("""INSERT INTO vehicle_variants
          (variant_id,make,model,generation,engine_family,engine_code,transmission,drive,fuel,market,
           year_from,year_to,variant_key,source_class,confidence,source_url,notes,created_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(variant_id) DO UPDATE SET
            make=excluded.make,model=excluded.model,generation=excluded.generation,
            engine_family=excluded.engine_family,engine_code=excluded.engine_code,
            transmission=excluded.transmission,drive=excluded.drive,fuel=excluded.fuel,
            market=excluded.market,year_from=excluded.year_from,year_to=excluded.year_to,
            variant_key=excluded.variant_key,source_class=excluded.source_class,
            confidence=excluded.confidence,source_url=excluded.source_url,
            notes=excluded.notes""",
          (v["variant_id"],v["make"],v["model"],v["generation"],v["engine_family"],v["engine_code"],
           v["transmission"],v["drive"],v["fuel"],v["market"],v["year_from"],v["year_to"],key,
           v["source_class"],v["confidence"],v["source_url"],v["notes"],now))
        c.execute("""INSERT INTO vehicle_variant_map
          (vehicle_id,variant_id,match_method,confidence,created_at)
          VALUES(?,?,?,?,?)
          ON CONFLICT(vehicle_id) DO UPDATE SET
            variant_id=excluded.variant_id,match_method=excluded.match_method,
            confidence=excluded.confidence,created_at=excluded.created_at""",
          (v["variant_id"],v["variant_id"],"canonical-profile","CONTRASTADO",now))
    catalog_rows=c.execute("SELECT id,make,model FROM vehicles").fetchall()
    for row in catalog_rows:
        nm=normalize((row["make"] or "")+" "+(row["model"] or ""))
        target=None
        if "hyundai" in nm and "kona" in nm and "sx2" in nm and ("hev" in nm or "hybrid" in nm):
            target="car/hyundai/kona-sx2-hev-2025"
        elif "kia" in nm and "niro" in nm and ("sg2" in nm or "hev" in nm or "hybrid" in nm):
            target="car/kia/niro-sg2-hev-2024"
        elif "bmw" in nm and "3 series 320d" in nm:
            target="car/bmw/3-series-320d"
        if target:
            c.execute("INSERT INTO vehicle_variant_map(vehicle_id,variant_id,match_method,confidence,created_at) VALUES(?,?,?,?,?) ON CONFLICT(vehicle_id) DO UPDATE SET variant_id=excluded.variant_id,match_method=excluded.match_method,confidence=excluded.confidence,created_at=excluded.created_at",
                      (row["id"],target,"catalog-explicit-model-signals","ALTA",now))
    c.commit(); c.close()


def ensure_source_document(title,url,variant_id=None,source_class=None):
    """Registra de forma idempotente el documento fuente, sin inventar sección/aplicabilidad."""
    if not url:
        return None
    c=db()
    row=c.execute(
        "SELECT document_id FROM source_documents WHERE variant_id IS ? AND url=? ORDER BY document_id DESC LIMIT 1",
        (variant_id,url)
    ).fetchone()
    if row:
        c.close()
        return row["document_id"]
    now=datetime.now(timezone.utc).isoformat()
    c.execute(
        """INSERT INTO source_documents(variant_id,title,url,source_class,retrieved_at)
           VALUES(?,?,?,?,?)""",
        (variant_id,title,url,source_class,now)
    )
    did=c.execute("SELECT last_insert_rowid()").fetchone()[0]
    c.commit(); c.close()
    return did


def ensure_evidence_variant_links():
    """Backfill de evidencia existente hacia la variante técnica canónica."""
    c=db()
    c.execute("""
      UPDATE evidence
      SET variant_id=(SELECT vvm.variant_id FROM vehicle_variant_map vvm WHERE vvm.vehicle_id=evidence.vehicle_id)
      WHERE variant_id IS NULL
        AND EXISTS (SELECT 1 FROM vehicle_variant_map vvm WHERE vvm.vehicle_id=evidence.vehicle_id)
    """)
    c.commit(); c.close()


def ensure_technical_variant_links():
    """Backfill de registros existentes hacia la variante técnica canónica."""
    c=db()
    c.execute("""
      UPDATE technical_records
      SET variant_id=(
        SELECT vvm.variant_id
        FROM vehicle_variant_map vvm
        WHERE vvm.vehicle_id=technical_records.vehicle_id
      )
      WHERE variant_id IS NULL
        AND EXISTS (
          SELECT 1 FROM vehicle_variant_map vvm
          WHERE vvm.vehicle_id=technical_records.vehicle_id
        )
    """)
    c.commit(); c.close()

def ensure_technical_source_documents():
    """Registra los documentos fuente declarados por los registros técnicos, sin inventar metadatos."""
    c=db()
    rows=c.execute("""
      SELECT DISTINCT variant_id, source_title, source_url, source_class
      FROM technical_records
      WHERE variant_id IS NOT NULL AND source_url IS NOT NULL AND source_url <> ''
    """).fetchall()
    c.close()
    for row in rows:
        ensure_source_document(row["source_title"], row["source_url"], row["variant_id"], row["source_class"])


def get_vehicle_variant(vehicle_id):
    c=db()
    row=c.execute("""SELECT vv.*, vvm.match_method, vvm.confidence AS match_confidence
                     FROM vehicle_variant_map vvm
                     JOIN vehicle_variants vv ON vv.variant_id=vvm.variant_id
                     WHERE vvm.vehicle_id=?""",(vehicle_id,)).fetchone()
    c.close()
    return dict(row) if row else None

def resolve_variant_signals(signals):
    """Resuelve una variante solo cuando las señales disponibles son compatibles."""
    make=normalize(signals.get("make"))
    model=normalize(signals.get("model"))
    engine=normalize(signals.get("engine"))
    engine_code=normalize(signals.get("engine_code"))
    transmission=normalize(signals.get("transmission"))
    drive=normalize(signals.get("drive"))
    fuel=normalize(signals.get("fuel"))
    market=normalize(signals.get("market"))
    try: year=int(str(signals.get("year") or "").strip())
    except Exception: year=None
    c=db()
    rows=c.execute("SELECT * FROM vehicle_variants").fetchall()
    c.close()

    # Un código de motor compartido no basta para elegir una variante si
    # tampoco conocemos el modelo. Por ejemplo, G4LL aparece en KONA y Niro.
    # Permitimos resolver por código cuando las señales disponibles dejan una
    # sola variante compatible; si el código sigue siendo ambiguo, no elegimos.
    if engine_code and not model:
        shared = [
            row for row in rows
            if engine_code == normalize(row["engine_code"])
        ]
        if len({row["variant_id"] for row in shared}) > 1:
            return None

    ranked=[]
    for row in rows:
        score=0
        conflicts=0
        rmake=normalize(row["make"]); rmodel=normalize(row["model"])
        if make:
            if make==rmake: score+=5
            else: conflicts+=3
        if model:
            if model==rmodel or model in rmodel or rmodel in model: score+=5
            else: conflicts+=3
        if engine_code:
            if engine_code==normalize(row["engine_code"]): score+=8
            elif engine_code not in normalize(row["engine_code"]): conflicts+=2
        if engine:
            ef=normalize(row["engine_family"])
            if engine==ef or engine in ef or ef in engine: score+=5
        if transmission:
            rt=normalize(row["transmission"])
            # Las fuentes públicas no usan siempre el mismo orden/formato
            # (p. ej. "DCT 6" frente a "6-speed DCT").
            t_tokens=set(transmission.split())
            r_tokens=set(rt.split())
            if transmission==rt or transmission in rt or rt in transmission or t_tokens.issubset(r_tokens) or r_tokens.issubset(t_tokens):
                score+=3
        if drive:
            if drive==normalize(row["drive"]): score+=3
        if fuel:
            if fuel==normalize(row["fuel"]): score+=2
        if market:
            if market==normalize(row["market"]) or market in normalize(row["market"]): score+=1
        if year:
            try:
                yf=int(row["year_from"]); yt=int(row["year_to"])
                if yf<=year<=yt: score+=3
                else: conflicts+=2
            except Exception: pass
        if score>=10 and conflicts<4:
            ranked.append((score,conflicts,dict(row)))
    ranked.sort(key=lambda x:(-x[0],x[1]))
    if not ranked:
        return None
    best=ranked[0]
    if len(ranked)>1 and best[0]==ranked[1][0] and best[1]==ranked[1][1]:
        return None
    result=best[2]
    result["resolution_score"]=best[0]
    result["resolution_conflicts"]=best[1]
    result["resolution_method"]="VIN / señales públicas"
    return result

def vehicle_variant_context(vehicle_id):
    c=db()
    v=c.execute("SELECT * FROM vehicles WHERE id=?",(vehicle_id,)).fetchone()
    c.close()
    if not v:
        return None
    raw={}
    try: raw=json.loads(v["raw_json"] or "{}")
    except Exception: pass
    return {
        "vehicle_id":vehicle_id,
        "make":v["make"],"model":v["model"],"years":v["years"],
        "body_types":v["body_types"],"availability":v["availability"],
        "raw":raw
    }

def find_technical_profile(vehicle_id, category=""):
    """Resuelve primero una variante canónica; después el vehículo del catálogo."""
    ctx=vehicle_variant_context(vehicle_id)
    if not ctx:
        direct=get_vehicle_variant(vehicle_id)
        if direct:
            profile=direct["variant_id"]
            c=db()
            n=c.execute(
                "SELECT COUNT(*) n FROM technical_records WHERE (variant_id=? OR vehicle_id=?)"+(" AND category=?" if category else ""),
                (profile,profile,category) if category else (profile,profile)
            ).fetchone()["n"]
            c.close()
            return {
                "profile_vehicle_id":profile,
                "variant_id":profile,
                "match":"canonical-profile",
                "confidence":direct.get("confidence") or direct.get("match_confidence"),
                "reason":"canonical_variant_id",
                "has_technical_data":bool(n)
            }
        return {"profile_vehicle_id":None,"match":"none","reason":"vehicle_not_found"}
    c=db()
    mapped=c.execute("""SELECT variant_id,match_method,confidence
                       FROM vehicle_variant_map WHERE vehicle_id=?""",(vehicle_id,)).fetchone()
    if mapped:
        profile=mapped["variant_id"]
        n=c.execute("SELECT COUNT(*) n FROM technical_records WHERE vehicle_id=?"+(" AND category=?" if category else ""),
                    (profile,category) if category else (profile,)).fetchone()["n"]
        if n:
            c.close()
            return {"profile_vehicle_id":profile,"variant_id":profile,"match":mapped["match_method"],
                    "confidence":mapped["confidence"],"reason":"vehicle_variant_map"}
    exact_row=c.execute(
        """SELECT variant_id
           FROM technical_records
           WHERE vehicle_id=?
           ORDER BY CASE WHEN variant_id IS NULL THEN 1 ELSE 0 END, id
           LIMIT 1""",
        (vehicle_id,)
    ).fetchone()
    if exact_row:
        exact_variant=exact_row["variant_id"] or vehicle_id
        c.close()
        return {"profile_vehicle_id":exact_variant,"variant_id":exact_variant,
                "match":"exact","reason":"exact_technical_records"}
    raw=ctx.get("raw") or {}
    resolved=resolve_variant_signals({
        "make":ctx.get("make"),"model":ctx.get("model"),
        "year":raw.get("year") or raw.get("model_year"),
        "engine":raw.get("engine") or raw.get("engine_name"),
        "engine_code":raw.get("engine_code"),
        "transmission":raw.get("transmission"),"drive":raw.get("drive"),
        "fuel":raw.get("fuel"),"market":raw.get("market")
    })
    if resolved:
        profile=resolved["variant_id"]
        n=c.execute("SELECT COUNT(*) n FROM technical_records WHERE vehicle_id=?"+(" AND category=?" if category else ""),
                    (profile,category) if category else (profile,)).fetchone()["n"]
        if n:
            c.close()
            return {"profile_vehicle_id":profile,"variant_id":profile,"match":"signals","confidence":"ALTA","reason":"technical_signals"}
    c.close()
    return {"profile_vehicle_id":None,"match":"none","reason":"no_verified_profile"}


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
        ensure_seed_kia_niro_vehicle()
        ensure_seed_variants()
        ensure_technical_variant_links()
        ensure_evidence_variant_links()
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

def seed_verified_hyundai_kona_sx2_hev():
    # Seed incremental e idempotente: añade solo registros que todavía no existan.
    # Así una actualización de datos no duplica información en instalaciones existentes.
    vehicle_id = "car/hyundai/kona-sx2-hev-2025"
    seed_version = "4"
    if meta_get("hyundai_kona_maintenance_seed") == seed_version:
        return
    now = datetime.now(timezone.utc).isoformat()
    rows = [
      ("torque","Tuercas de rueda","107–127","Nm","Cambiar un neumático — KONA SX2 HEV 2025","https://ownersmanual.hyundai.com/full_webhelp/SX2HEV/2025/es_ES/idde3c3d57812.html","FABRICANTE / OEM","CONTRASTADO · EUROPA","2025","2025","Par de apriete de las tuercas de rueda. El manual también lo expresa como 11–13 kgf·m / 79–94 lbf·ft."),
      ("technical specifications","Motor","1.6 GDi HEV","", "Nuevo Hyundai KONA — Características técnicas SX2 HEV","https://www.hyundai.es/catalogo/nuevo-kona.pdf","FABRICANTE / OEM","CONTRASTADO","2025","2025","Ficha oficial Hyundai España."),
      ("technical specifications","Cilindrada","1580","cm³", "Nuevo Hyundai KONA — Características técnicas SX2 HEV","https://www.hyundai.es/catalogo/nuevo-kona.pdf","FABRICANTE / OEM","CONTRASTADO","2025","2025","4 cilindros en línea."),
      ("technical specifications","Potencia combinada","141","CV", "Nuevo Hyundai KONA — Características técnicas SX2 HEV","https://www.hyundai.es/catalogo/nuevo-kona.pdf","FABRICANTE / OEM","CONTRASTADO","2025","2025","104 kW de potencia total combinada."),
      ("technical specifications","Par combinado","265","Nm", "Nuevo Hyundai KONA — Características técnicas SX2 HEV","https://www.hyundai.es/catalogo/nuevo-kona.pdf","FABRICANTE / OEM","CONTRASTADO","2025","2025","Par máximo combinado publicado por Hyundai."),
      ("technical specifications","Transmisión","DCT 6","", "Nuevo Hyundai KONA — Características técnicas SX2 HEV","https://www.hyundai.es/catalogo/nuevo-kona.pdf","FABRICANTE / OEM","CONTRASTADO","2025","2025","Automático de doble embrague y 6 velocidades."),
      ("technical specifications","Tracción","FWD","", "Nuevo Hyundai KONA — Características técnicas SX2 HEV","https://www.hyundai.es/catalogo/nuevo-kona.pdf","FABRICANTE / OEM","CONTRASTADO","2025","2025","Tracción delantera."),
      ("technical specifications","Código motor","G4LL","", "Cruce VIN Hyundai KONA SX2 HEV","https://www.hyundai.es/catalogo/nuevo-kona.pdf","FABRICANTE / OEM","ALTA · CRUCE ESTRUCTURAL","2025","2025","Cruce experimental VDS/estructura; no se presenta como identificación OEM del número de serie."),
      ("lubricants","Aceite motor","3,8","L", "Lubricantes y cantidades recomendados — KONA Hybrid 2025","https://ownersmanual.hyundai.com/full_webhelp/SX2HEV/2025/es_ES/ideebd8cfc412.html","FABRICANTE / OEM","CONTRASTADO","2025","2025","Drenaje y llenado; SAE 5W-30, ACEA A5/B5."),
      ("lubricants","Especificación aceite motor","SAE 5W-30, ACEA A5/B5","", "Lubricantes y cantidades recomendados — KONA Hybrid 2025","https://ownersmanual.hyundai.com/full_webhelp/SX2HEV/2025/es_ES/ideebd8cfc412.html","FABRICANTE / OEM","CONTRASTADO","2025","2025","Aceite totalmente sintético; seguir especificación del manual."),
      ("lubricants","Líquido DCT","1,6-1,7","L", "Lubricantes y cantidades recomendados — KONA Hybrid 2025","https://ownersmanual.hyundai.com/full_webhelp/SX2HEV/2025/es_ES/ideebd8cfc412.html","FABRICANTE / OEM","CONTRASTADO","2025","2025","HK D DCTF TGO-10 PLUS (SK), SPIRAX S6 GHDE 70W DCTF PLUS (SHELL) o Hyundai Genuine DCTF 70W SYNTHETIC PLUS."),
      ("lubricants","Refrigerante motor","7,2","L", "Lubricantes y cantidades recomendados — KONA Hybrid 2025","https://ownersmanual.hyundai.com/full_webhelp/SX2HEV/2025/es_ES/ideebd8cfc412.html","FABRICANTE / OEM","CONTRASTADO","2025","2025","Mezcla de anticongelante y agua destilada; base etilenglicol para radiadores de aluminio."),
      ("lubricants","Líquido de frenos","Según sea necesario","", "Lubricantes y cantidades recomendados — KONA Hybrid 2025","https://ownersmanual.hyundai.com/full_webhelp/SX2HEV/2025/es_ES/ideebd8cfc412.html","FABRICANTE / OEM","CONTRASTADO","2025","2025","DOT-4."),
      ("lubricants","Combustible","38","L", "Lubricantes y cantidades recomendados — KONA Hybrid 2025","https://ownersmanual.hyundai.com/full_webhelp/SX2HEV/2025/es_ES/ideebd8cfc412.html","FABRICANTE / OEM","CONTRASTADO","2025","2025","Capacidad publicada en el manual."),
      ("lubricants","Aceite diferencial trasero","0,4-0,5","L", "Lubricantes y cantidades recomendados — KONA Hybrid 2025","https://ownersmanual.hyundai.com/full_webhelp/SX2HEV/2025/es_ES/ideebd8cfc412.html","FABRICANTE / OEM","CONTRASTADO","2025","2025","API GL-5 SAE 75W/85; SK HCT-5 75W/85 o equivalente."),
      ("lubricants","Líquido actuador embrague motor","Según sea necesario","", "Lubricantes y cantidades recomendados — KONA Hybrid 2025","https://ownersmanual.hyundai.com/full_webhelp/SX2HEV/2025/es_ES/ideebd8cfc412.html","FABRICANTE / OEM","CONTRASTADO","2025","2025","SAE J1704 DOT-4LV / FMVSS 116 DOT-4 / ISO4926 CLASS-6."),
      ("maintenance","Aceite y filtro de motor","Cada 15.000 km o 12 meses","", "Calendario europeo de mantenimiento — KONA Hybrid 2025","https://www.hyundai.rs/upload/document/korisnicko_uputstvo_kona.pdf","FABRICANTE / OEM","CONTRASTADO · TABLA EUROPEA","2025","2025","Lo que ocurra primero. Fuente europea del manual; no mezclar con calendarios de otros mercados."),
      ("maintenance","Correa HSG","Inspección: 15.000 km o 12 meses; sustitución: 105.000 km o 48 meses","", "Calendario europeo de mantenimiento — KONA Hybrid 2025","https://www.hyundai.rs/upload/document/korisnicko_uputstvo_kona.pdf","FABRICANTE / OEM","CONTRASTADO · TABLA EUROPEA","2025","2025","Lo que ocurra primero."),
      ("maintenance","Aditivo de combustible","Cada 15.000 km o 12 meses cuando corresponda","", "Calendario europeo de mantenimiento — KONA Hybrid 2025","https://www.hyundai.rs/upload/document/korisnicko_uputstvo_kona.pdf","FABRICANTE / OEM","CONTRASTADO · TABLA EUROPEA","2025","2025","Aplicable según combustible EN228 y condiciones indicadas por Hyundai."),
      ("maintenance","Filtro de aire del motor","Inspección: 15.000/45.000/75.000/105.000 km; sustitución: 30.000/60.000/90.000/120.000 km","", "Calendario europeo de mantenimiento — KONA Hybrid 2025","https://www.hyundai.rs/upload/document/korisnicko_uputstvo_kona.pdf","FABRICANTE / OEM","CONTRASTADO · TABLA EUROPEA","2025","2025","Hitos expresados en km; aplicar también el límite temporal del calendario cuando corresponda."),
      ("maintenance","Refrigerante motor/sistema de batería","Primera sustitución: 195.000 km o 120 meses; después cada 30.000 km o 24 meses","", "Calendario europeo de mantenimiento — KONA Hybrid 2025","https://www.hyundai.rs/upload/document/korisnicko_uputstvo_kona.pdf","FABRICANTE / OEM","CONTRASTADO · TABLA EUROPEA","2025","2025","Corrección documentada: no usar 180.000 km/10 años para este SX2HEV europeo."),
      ("maintenance","Líquido de frenos","Inspección: 15.000/30.000/60.000/75.000/105.000/120.000 km; sustitución: 45.000/90.000 km","", "Calendario europeo de mantenimiento — KONA Hybrid 2025","https://www.hyundai.rs/upload/document/korisnicko_uputstvo_kona.pdf","FABRICANTE / OEM","CONTRASTADO · TABLA EUROPEA","2025","2025","Inspección y sustitución según tabla europea."),
      ("maintenance","Batería","Comprobación en cada intervalo de mantenimiento periódico","", "Calendario europeo de mantenimiento — KONA Hybrid 2025","https://www.hyundai.rs/upload/document/korisnicko_uputstvo_kona.pdf","FABRICANTE / OEM","CONTRASTADO · TABLA EUROPEA","2025","2025","Comprobación periódica."),
      ("maintenance","Conductos y conexiones de freno","Inspección en cada intervalo de mantenimiento periódico","", "Calendario europeo de mantenimiento — KONA Hybrid 2025","https://www.hyundai.rs/upload/document/korisnicko_uputstvo_kona.pdf","FABRICANTE / OEM","CONTRASTADO · TABLA EUROPEA","2025","2025","Revisión visual/estado según calendario."),
      ("maintenance","Discos y pastillas de freno","Inspección en cada intervalo de mantenimiento periódico","", "Calendario europeo de mantenimiento — KONA Hybrid 2025","https://www.hyundai.rs/upload/document/korisnicko_uputstvo_kona.pdf","FABRICANTE / OEM","CONTRASTADO · TABLA EUROPEA","2025","2025","Inspección periódica."),
      ("maintenance","Palieres y fuelles","Inspección a 30.000/60.000/90.000/120.000 km","", "Calendario europeo de mantenimiento — KONA Hybrid 2025","https://www.hyundai.rs/upload/document/korisnicko_uputstvo_kona.pdf","FABRICANTE / OEM","CONTRASTADO · TABLA EUROPEA","2025","2025","Inspección de palieres y fuelles."),
      ("maintenance","Bujías","Según calendario europeo; intervalo exacto pendiente de contraste específico","", "Manual del propietario KONA Hybrid 2025","https://ownersmanual.hyundai.com/full_webhelp/SX2HEV/2025/es_ES/indexterm.html","FABRICANTE / OEM","CONTRASTADO · INTERVALO PENDIENTE","2025","2025","El elemento está incluido, pero no se incorpora un intervalo no verificado."),
      ("maintenance","Líquido actuador embrague motor","Según calendario europeo; intervalo exacto pendiente de contraste específico","", "Manual del propietario KONA Hybrid 2025","https://ownersmanual.hyundai.com/full_webhelp/SX2HEV/2025/es_ES/indexterm.html","FABRICANTE / OEM","CONTRASTADO · INTERVALO PENDIENTE","2025","2025","El manual exige comprobación de nivel; no se inventa un intervalo de sustitución."),
      ("maintenance","Filtro de aire del habitáculo","Según calendario de mantenimiento","", "Mantenimiento del sistema — KONA Hybrid 2025","https://ownersmanual.hyundai.com/full_webhelp/SX2HEV/2025/es_ES/id28824ad7b0a.html","FABRICANTE / OEM","CONTRASTADO · INTERVALO PENDIENTE","2025","2025","Hyundai indica seguir el calendario y acortar en condiciones adversas."),
      ("maintenance","Líquido DCT","Según calendario europeo; intervalo exacto pendiente de contraste específico","", "Manual del propietario KONA Hybrid 2025","https://ownersmanual.hyundai.com/full_webhelp/SX2HEV/2025/es_ES/indexterm.html","FABRICANTE / OEM","CONTRASTADO · INTERVALO PENDIENTE","2025","2025","No se inventa un intervalo de sustitución en uso normal."),
      ("maintenance","Refrigerante del aire acondicionado","Según calendario de mantenimiento","", "Manual del propietario KONA Hybrid 2025","https://ownersmanual.hyundai.com/full_webhelp/SX2HEV/2025/es_ES/indexterm.html","FABRICANTE / OEM","CONTRASTADO · INTERVALO PENDIENTE","2025","2025","El servicio del refrigerante debe realizarlo personal formado.")
    ]
    c = db()
    for row in rows:
        category,field = row[0], row[1]
        exists = c.execute(
            "SELECT 1 FROM technical_records WHERE vehicle_id=? AND category=? AND field=? LIMIT 1",
            (vehicle_id,category,field)
        ).fetchone()
        if exists:
            continue
        c.execute("""INSERT INTO technical_records
          (vehicle_id,category,field,value,unit,source_title,source_url,source_class,confidence,applicable_from,applicable_to,notes,created_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (vehicle_id,category,field,row[2],row[3],row[4],row[5],row[6],row[7],row[8],row[9],row[10],now))
    c.commit()
    c.close()
    meta_set("hyundai_kona_maintenance_seed",seed_version)

def seed_verified_hyundai_kona_sx2_hev_electrical():
    vehicle_id = "car/hyundai/kona-sx2-hev-2025"
    seed_version = "1"
    meta_key = "hyundai_kona_electrical_seed"
    if meta_get(meta_key) == seed_version:
        return
    now = datetime.now(timezone.utc).isoformat()
    rows = [
      ("electrical","DCT 1","40 A","Panel de fusibles compartimento motor","https://ownersmanual.hyundai.com/full_webhelp/SX2HEV/2025/es_ES/id4df25f244f1.html","TCM"),
      ("electrical","DCT 2","40 A","Panel de fusibles compartimento motor","https://ownersmanual.hyundai.com/full_webhelp/SX2HEV/2025/es_ES/id4df25f244f1.html","TCM"),
      ("electrical","CLUTCH ACT","30 A","Panel de fusibles compartimento motor","https://ownersmanual.hyundai.com/full_webhelp/SX2HEV/2025/es_ES/id4df25f244f1.html","Actuador del embrague"),
      ("electrical","HEV ECU 1","15 A","Panel de fusibles compartimento motor","https://ownersmanual.hyundai.com/full_webhelp/SX2HEV/2025/es_ES/id4df25f244f1.html","HPCU"),
      ("electrical","HEV ECU 2","10 A","Bloque PCB compartimento motor","https://ownersmanual.hyundai.com/full_webhelp/SX2HEV/2025/es_ES/id4df25f244f1.html","HPCU y actuador del embrague"),
      ("electrical","EWP 1","10 A","Panel de fusibles compartimento motor","https://ownersmanual.hyundai.com/full_webhelp/SX2HEV/2025/es_ES/id4df25f244f1.html","Bomba de agua electrónica del motor"),
      ("electrical","EWP 2","7,5 A","Panel de fusibles compartimento motor","https://ownersmanual.hyundai.com/full_webhelp/SX2HEV/2025/es_ES/id4df25f244f1.html","Bomba de agua electrónica HEV"),
      ("electrical","IEB 1","60 A","Panel de fusibles compartimento motor","https://ownersmanual.hyundai.com/full_webhelp/SX2HEV/2025/es_ES/id4df25f244f1.html","Unidad IEB"),
      ("electrical","IEB 2","60 A","Panel de fusibles compartimento motor","https://ownersmanual.hyundai.com/full_webhelp/SX2HEV/2025/es_ES/id4df25f244f1.html","Unidad IEB"),
      ("electrical","AUX BATTERY","60 A","Panel de fusibles compartimento motor","https://ownersmanual.hyundai.com/full_webhelp/SX2HEV/2025/es_ES/id4df25f244f1.html","Batería auxiliar de litio de 12 V"),
      ("electrical","ECU 1","20 A","Bloque PCB compartimento motor","https://ownersmanual.hyundai.com/full_webhelp/SX2HEV/2025/es_ES/id4df25f244f1.html","ECM"),
      ("electrical","IGN COIL","20 A","Bloque PCB compartimento motor","https://ownersmanual.hyundai.com/full_webhelp/SX2HEV/2025/es_ES/id4df25f244f1.html","Inyectores 1 a 4"),
      ("electrical","SENSOR 1","15 A","Bloque PCB compartimento motor","https://ownersmanual.hyundai.com/full_webhelp/SX2HEV/2025/es_ES/id4df25f244f1.html","Sensor de oxígeno arriba/abajo"),
      ("electrical","TCU 2","15 A","Bloque PCB compartimento motor","https://ownersmanual.hyundai.com/full_webhelp/SX2HEV/2025/es_ES/id4df25f244f1.html","TCM"),
      ("electrical","FCA","10 A","Bloque PCB compartimento motor","https://ownersmanual.hyundai.com/full_webhelp/SX2HEV/2025/es_ES/id4df25f244f1.html","Unidad de radar delantero")
    ]
    c=db()
    for category,field,value,location,url,circuit in rows:
        exists=c.execute("SELECT 1 FROM technical_records WHERE vehicle_id=? AND category=? AND field=? LIMIT 1",(vehicle_id,category,field)).fetchone()
        if exists: continue
        c.execute("""INSERT INTO technical_records
          (vehicle_id,category,field,value,unit,source_title,source_url,source_class,confidence,applicable_from,applicable_to,notes,created_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (vehicle_id,category,field,value,"", "Descripción del panel de fusibles/relés — KONA Hybrid 2025",
           url,"FABRICANTE / OEM","CONTRASTADO","2025","2025",location+" · Circuito protegido: "+circuit,now))
    c.commit(); c.close()
    meta_set(meta_key,seed_version)

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


def ensure_seed_kia_niro_vehicle():
    # El catálogo VehiclesDB se refresca desde cero. Esta entrada garantiza
    # que el perfil técnico contrastado del Niro siga siendo seleccionable
    # aunque el catálogo abierto no incluya exactamente esta variante.
    vehicle_id = "car/kia/niro-sg2-hev-2024"
    c = db()
    row = c.execute("SELECT 1 FROM vehicles WHERE id=?", (vehicle_id,)).fetchone()
    if not row:
        raw = {
            "id": vehicle_id,
            "name": "Niro SG2 HEV",
            "body_types": ["SUV"],
            "years": "2022–2026",
            "source": "Kia public documentation"
        }
        c.execute("""INSERT OR REPLACE INTO vehicles
          (id,make,model,kind,body_types,years,availability,popularity,sources,raw_json)
          VALUES(?,?,?,?,?,?,?,?,?,?)""",
          (vehicle_id,"Kia","Niro SG2 HEV","car","SUV","2022–2026",
           "[\"ES\",\"EU\"]","verified-seed",
           "Kia public documentation",json.dumps(raw,ensure_ascii=False)))
        c.commit()
    c.close()

def seed_verified_kia_niro_sg2_hev():
    vehicle_id = "car/kia/niro-sg2-hev-2024"
    seed_version = "3"
    ensure_seed_kia_niro_vehicle()
    if meta_get("kia_niro_sg2_seed") == seed_version:
        return
    now = datetime.now(timezone.utc).isoformat()
    rows = [
      ("torque","Tuercas de rueda","107–127","Nm","Neumáticos y ruedas — Niro SG2 2024","https://ownersmanual.kia.com/full_webhelp/SG2/2024/es_ES/topics/chapter10_7.html","FABRICANTE / OEM","CONTRASTADO · EUROPA","2024","2024","Par de apriete de las tuercas de rueda. El manual también lo expresa como 11–13 kgf·m / 79–94 lbf·ft."),
      ("torque","Tornillo masa TCU (GC102)","7,8–9,8","Nm","TSB TRA106 — 6-Speed DCT judgement logic improvement / ground bolt tightening","https://static.nhtsa.gov/odi/tsbs/2023/MC-10231842-0001.pdf","TSB KIA / NHTSA","CONTRASTADO · TSB PÚBLICO","2023","2023","Niro P/HEV (SG2 P/HEV). Rango de producción afectado: 14/06/2022–12/12/2022. Usar como especificación del TSB para GC102; no generalizar fuera del procedimiento aplicable."),
      ("torque","Tornillo masa actuador embrague (GC103)","9,8–11,8","Nm","TSB TRA106 — 6-Speed DCT judgement logic improvement / ground bolt tightening","https://static.nhtsa.gov/odi/tsbs/2023/MC-10231842-0001.pdf","TSB KIA / NHTSA","CONTRASTADO · TSB PÚBLICO","2023","2023","Niro P/HEV (SG2 P/HEV). Rango de producción afectado: 14/06/2022–12/12/2022. Usar como especificación del TSB para GC103; no generalizar fuera del procedimiento aplicable."),
      ("torque","Tornillos superiores de carcasa EGR","3,9","Nm","TSB SC305 — EGR control valve pipe and hose replacement","https://static.nhtsa.gov/odi/tsbs/2024/MC-11006369-0001.pdf","TSB KIA / NHTSA","CONTRASTADO · TSB PÚBLICO","2024","2024","Niro P/HEV (SG2 P/HEV). Tres tornillos T-25; el TSB indica sustituir los tornillos retirados y aplicar 3,9 N·m al montaje."),
      ("technical specifications","Motor","Smartstream 1.6 GDi HEV","", "Kia Niro — especificaciones","https://www.kia.com/es/modelos/niro/descubrelo/","FABRICANTE / OEM","CONTRASTADO","2022","2026","Motor gasolina 1.6 GDI; sistema HEV."),
      ("technical specifications","Cilindrada","1580","cm³","El nuevo Kia Niro impulsa la movilidad sostenible","https://press.kia.com/es/es/home/notas-de-prensa/press-releases/2022/la-sostenibilidad-simplificada--el-nuevo-kia-niro-acelerara-la-t.html","FABRICANTE / OEM","CONTRASTADO","2022","2026","Cilindrada."),
      ("technical specifications","Potencia combinada","141","CV","El nuevo Kia Niro impulsa la movilidad sostenible","https://press.kia.com/es/es/home/notas-de-prensa/press-releases/2022/la-sostenibilidad-simplificada--el-nuevo-kia-niro-acelerara-la-t.html","FABRICANTE / OEM","CONTRASTADO","2022","2026","HEV: 141 CV."),
      ("technical specifications","Par combinado","265","Nm","El nuevo Kia Niro impulsa la movilidad sostenible","https://press.kia.com/es/es/home/notas-de-prensa/press-releases/2022/la-sostenibilidad-simplificada--el-nuevo-kia-niro-acelerara-la-t.html","FABRICANTE / OEM","CONTRASTADO","2022","2026","Par máximo combinado."),
      ("technical specifications","Transmisión","6DCT","", "Kia Niro — especificaciones","https://www.kia.com/es/modelos/niro/descubrelo/","FABRICANTE / OEM","CONTRASTADO","2022","2026","Doble embrague, 6 velocidades."),
      ("technical specifications","Tracción","FWD","", "Kia Niro — especificaciones","https://www.kia.com/es/modelos/niro/descubrelo/","FABRICANTE / OEM","CONTRASTADO","2022","2026","Tracción delantera."),
      ("maintenance","Aceite y filtro de motor","Cada 15.000 km o 12 meses","", "Servicio de mantenimiento programado — Niro SG2 2024","https://ownersmanual.kia.com/full_webhelp/SG2/2024/es_ES/topics/chapter9_4.html","FABRICANTE / OEM","CONTRASTADO · EUROPA","2024","2026","Lo que ocurra primero."),
      ("maintenance","Refrigerante motor","Primero 180.000 km o 120 meses; después 30.000 km o 24 meses","", "Servicio de mantenimiento programado — Niro SG2 2024","https://ownersmanual.kia.com/full_webhelp/SG2/2024/es_ES/topics/chapter9_4.html","FABRICANTE / OEM","CONTRASTADO · EUROPA","2024","2026","Calendario normal europeo."),
      ("maintenance","Refrigerante inversor HEV","Primero 180.000 km o 120 meses; después 30.000 km o 24 meses","", "Servicio de mantenimiento programado — Niro SG2 2024","https://ownersmanual.kia.com/full_webhelp/SG2/2024/es_ES/topics/chapter9_4.html","FABRICANTE / OEM","CONTRASTADO · EUROPA","2024","2026","Calendario normal europeo."),
      ("maintenance","Correa HSG","Inspeccionar 15.000 km/12 meses; sustituir 105.000 km/48 meses","", "Servicio de mantenimiento programado — Niro SG2 2024","https://ownersmanual.kia.com/full_webhelp/SG2/2024/es_ES/topics/chapter9_4.html","FABRICANTE / OEM","CONTRASTADO · EUROPA","2024","2026","Hybrid Starter & Generator."),
      ("maintenance","Bujías","Cada 150.000 km","", "Servicio de mantenimiento programado — Niro SG2 2024","https://ownersmanual.kia.com/full_webhelp/SG2/2024/es_ES/topics/chapter9_4.html","FABRICANTE / OEM","CONTRASTADO · EUROPA","2024","2026","Calendario normal europeo."),
      ("maintenance","Líquido DCT","Sin comprobación ni mantenimiento en uso normal","", "Servicio de mantenimiento programado — Niro SG2 2024","https://ownersmanual.kia.com/full_webhelp/SG2/2024/es_ES/topics/chapter9_4.html","FABRICANTE / OEM","CONTRASTADO · EUROPA","2024","2026","No confundir con el líquido del actuador del embrague."),
      ("maintenance","Actuador del embrague del motor","Inspección 15/45/75/105 mil km; sustitución 30/60/90/120 mil km","", "Servicio de mantenimiento programado — Niro SG2 2024","https://ownersmanual.kia.com/full_webhelp/SG2/2024/es_ES/topics/chapter9_4.html","FABRICANTE / OEM","CONTRASTADO · EUROPA","2024","2026","Patrón de la tabla europea."),
      ("maintenance","Líquido de frenos","Inspección 15/45/75/105 mil km; sustitución 30/60/90/120 mil km","", "Servicio de mantenimiento programado — Niro SG2 2024","https://ownersmanual.kia.com/full_webhelp/SG2/2024/es_ES/topics/chapter9_4.html","FABRICANTE / OEM","CONTRASTADO · EUROPA","2024","2026","Patrón de la tabla europea."),
      ("technical specifications","Código de motor","G4LL","", "Documento público de homologación Niro C5P11","https://www.gov.il/BlobFolder/policy/25-0737/he/25-0737.pdf","HOMOLOGACIÓN / FUENTE PÚBLICA","MEDIA","2025","2025","No es confirmación OEM; requiere cruce adicional con documentación de reparación.")
    ]
    c=db()
    c.execute("DELETE FROM technical_records WHERE vehicle_id=?", (vehicle_id,))
    for category,field,value,unit,title,url,source_class,confidence,af,at,notes in rows:
        c.execute("""INSERT INTO technical_records
          (vehicle_id,category,field,value,unit,source_title,source_url,source_class,confidence,applicable_from,applicable_to,notes,created_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (vehicle_id,category,field,value,unit,title,url,source_class,confidence,af,at,notes,now))
    c.commit(); c.close()
    meta_set("kia_niro_sg2_seed",seed_version)


@app.on_event("startup")
def startup():
    init_db()
    seed_verified_bmw_g20_320d()
    seed_verified_hyundai_kona_sx2_hev()
    seed_verified_kia_niro_sg2_hev()
    seed_verified_hyundai_kona_sx2_hev_electrical()
    ensure_seed_vehicle()
    ensure_seed_kia_niro_vehicle()
    ensure_seed_variants()
    ensure_technical_variant_links()
    ensure_technical_source_documents()
    ensure_evidence_variant_links()
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
            if result.get("ok"):
                SYNC_STATE.update({
                    "sync":"listo",
                    "count":result.get("count",count_vehicles()),
                    "dataset":result.get("version",DATASET_VERSION),
                    "updated_at":datetime.now(timezone.utc).isoformat(),
                    "error":None
                })
                meta_set("dataset_sync","listo")
            else:
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
    """Busca aplicaciones del código/familia de motor sin mezclar variantes."""
    needle=normalize(q)
    like="%"+needle+"%"
    c=db()
    variants=c.execute(
        """SELECT vv.*, vvm.match_method, vvm.confidence AS match_confidence
           FROM vehicle_variants vv
           LEFT JOIN vehicle_variant_map vvm ON vvm.variant_id=vv.variant_id
           WHERE lower(vv.engine_code) LIKE ?
              OR lower(vv.engine_family) LIKE ?
              OR lower(vv.model) LIKE ?
              OR lower(vv.variant_key) LIKE ?
           ORDER BY vv.make COLLATE NOCASE, vv.model COLLATE NOCASE,
                    vv.year_from, vv.year_to
           LIMIT ?""",
        (like,like,like,like,limit)
    ).fetchall()
    variant_ids={r["variant_id"] for r in variants}
    remaining=max(0,limit-len(variants))
    catalog=[]
    if remaining:
        rows=c.execute(
            """SELECT id,make,model,kind,years,body_types,raw_json
               FROM vehicles
               WHERE lower(make || ' ' || model || ' ' || coalesce(raw_json,'')) LIKE ?
               ORDER BY make COLLATE NOCASE, model COLLATE NOCASE
               LIMIT ?""",
            (like,remaining)
        ).fetchall()
        for row in rows:
            if row["id"] not in variant_ids:
                catalog.append(dict(row))
    c.close()
    result=[]
    for row in variants:
        item=dict(row)
        item["id"]=row["variant_id"]
        item["kind"]="technical variant"
        item["years"]=(row["year_from"] or "") + ("–"+row["year_to"] if row["year_to"] and row["year_to"]!=row["year_from"] else "")
        item["body_types"]=""
        item["is_variant"]=True
        result.append(item)
    result.extend(catalog)
    return result[:limit]

def hyundai_test_vds(vin):
    return vin[2:8] == "HHA811"


def decode_hyundai_vin_crosscheck(vin):
    if vin[:3] != "KMH" or not hyundai_test_vds(vin):
        return None
    if vin[9] != "S":
        return None
    result = dict()
    result["matched"] = True
    result["manufacturer"] = "Hyundai Motor Company"
    result["model"] = "Kona SX2"
    result["variant"] = "HEV"
    result["model_year"] = 2025
    result["engine_code"] = "G4LL"
    result["engine"] = "1.6 GDi HEV"
    result["displacement_cc"] = "1580"
    result["cylinders"] = "4"
    result["transmission"] = "Automática DCT de 6 velocidades"
    result["drive"] = "Tracción delantera"
    result["fuel"] = "Gasolina híbrido"
    result["vds"] = vin[2:8]
    result["year_code"] = vin[9]
    result["plant_code"] = vin[10]
    result["plant"] = "Ulsan, Corea del Sur" if vin[10] == "U" else vin[10]
    result["scope"] = "VARIANTE IDENTIFICADA POR ESTRUCTURA VIN"
    result["limitation"] = "El número de serie exacto no se ha encontrado en una fuente pública independiente; no se inventa una versión o acabado."
    result["confidence"] = "ALTA · VDS + plataforma + año compatible"
    return result


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
    crosscheck = decode_hyundai_vin_crosscheck(vin)
    signals={
        "make":row.get("Make"),"model":row.get("Model"),"year":row.get("ModelYear"),
        "engine":row.get("EngineModel"),"engine_code":row.get("EngineModel"),
        "transmission":row.get("TransmissionStyle"),"drive":row.get("DriveType"),
        "fuel":row.get("FuelTypePrimary")
    }
    if crosscheck:
        signals.update({
            "make":"Hyundai",
            "model":crosscheck.get("model"),
            "year":crosscheck.get("model_year"),
            "engine":crosscheck.get("engine"),
            "engine_code":crosscheck.get("engine_code"),
            "transmission":crosscheck.get("transmission"),
            "drive":crosscheck.get("drive"),
            "fuel":crosscheck.get("fuel")
        })
    technical_variant=resolve_variant_signals(signals)
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
        "raw_count":len(results),
        "crosscheck":crosscheck,
        "technical_variant":technical_variant
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
    if row:
        return dict(row)
    variant=get_vehicle_variant(vehicle_id)
    if variant:
        item=dict(variant)
        item.update({
            "id":variant["variant_id"],
            "kind":"technical variant",
            "years":(variant.get("year_from") or "") + ("–"+variant["year_to"] if variant.get("year_to") and variant["year_to"]!=variant.get("year_from") else ""),
            "body_types":"",
            "availability":variant.get("market") or "",
            "sources":variant.get("source_class") or "",
            "raw_json":json.dumps({
                "generation":variant.get("generation"),
                "engine_family":variant.get("engine_family"),
                "engine_code":variant.get("engine_code"),
                "transmission":variant.get("transmission"),
                "drive":variant.get("drive"),
                "fuel":variant.get("fuel"),
                "market":variant.get("market")
            },ensure_ascii=False),
            "is_variant":True
        })
        return item
    return JSONResponse({"error":"Vehículo no encontrado"},status_code=404)

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
    requested_vehicle_id=vehicle_id
    profile=find_technical_profile(vehicle_id,category) if vehicle_id else {"profile_vehicle_id":None,"match":"none"}
    research_vehicle_id=profile.get("profile_vehicle_id") or vehicle_id
    # Cuando existe un perfil común, normalizamos también la consulta al nombre
    # canónico de ese perfil; así "Niro SG2", "Niro HEV" y variantes equivalentes
    # no generan cachés separados por simple diferencia de nomenclatura.
    variant=get_vehicle_variant(research_vehicle_id) if research_vehicle_id else None
    if variant:
        query=" ".join(x for x in [variant.get("make"),variant.get("model"),variant.get("generation"),
                                   variant.get("engine_family"),variant.get("engine_code"),
                                   variant.get("transmission"),variant.get("drive"),variant.get("market"),term] if x)
    elif profile.get("profile_vehicle_id") and profile.get("match")=="family":
        pctx=vehicle_variant_context(research_vehicle_id) or {}
        query=" ".join(x for x in [pctx.get("make"),pctx.get("model"),term] if x)
    else:
        query=" ".join(x for x in [make,model,year,engine,term] if x)
    # La caché técnica se asocia al perfil reutilizable cuando existe, no al
    # ID individual del catálogo. Así una familia/variante ya investigada no
    # dispara la misma búsqueda para cada fila de VehiclesDB.
    # Caché de evidencia: el catálogo es masivo y la profundidad técnica se
    # resuelve bajo demanda. Una búsqueda ya realizada para el mismo vehículo,
    # módulo y consulta se reutiliza durante 30 días en vez de volver a salir
    # a Internet cada vez que el usuario abre el módulo.
    now=datetime.now(timezone.utc)
    try:
        c=db()
        cached=c.execute(
            """SELECT title,url,domain,source_class,confidence,snippet,fetched_at
               FROM evidence
               WHERE variant_id IS ? AND category=? AND query=?
               ORDER BY id DESC LIMIT 20""",
            (variant.get("variant_id") if variant else None,category,query)
        ).fetchall()
        c.close()
        if cached:
            newest=cached[0]["fetched_at"]
            try:
                age=(now-datetime.fromisoformat(newest.replace("Z","+00:00"))).total_seconds()
            except Exception:
                age=999999999
            if age < 30*86400:
                return {"ok":True,"query":query,"results":[dict(x) for x in cached],"cached":True,"profile_vehicle_id":research_vehicle_id,"requested_vehicle_id":requested_vehicle_id}
    except Exception:
        pass
    try:
        results=search_web(query)
    except Exception as exc:
        return {"ok":False,"error":f"No se pudo consultar Internet: {exc}","results":[]}
    fetched_at=now.isoformat()
    c=db()
    for item in results:
        variant_id=variant.get("variant_id") if variant else None
        source_class=classify_source(item["url"])
        document_id=ensure_source_document(item["title"],item["url"],variant_id,source_class)
        c.execute(
            "INSERT INTO evidence(vehicle_id,variant_id,query,category,title,url,domain,source_class,confidence,snippet,fetched_at,document_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (research_vehicle_id,variant_id,query,category,item["title"],item["url"],urllib.parse.urlparse(item["url"]).netloc,
             source_class,"PENDIENTE DE CONTRASTE",item["snippet"],fetched_at,document_id)
        )
        item["source_class"]=classify_source(item["url"])
        item["confidence"]="PENDIENTE DE CONTRASTE"
        item["fetched_at"]=fetched_at
    c.commit()
    c.close()
    return {"ok":True,"query":query,"results":results,"cached":False,"profile_vehicle_id":research_vehicle_id,"requested_vehicle_id":requested_vehicle_id}

@app.get("/api/variant-dashboard/{vehicle_id:path}")
def variant_dashboard(vehicle_id:str):
    """Resumen de trabajo por módulo: dato contrastado, evidencia web o pendiente."""
    profile=find_technical_profile(vehicle_id)
    variant_id=profile.get("variant_id") or profile.get("profile_vehicle_id")
    if not variant_id:
        return JSONResponse({"ok":False,"error":"Variante técnica no resuelta"},status_code=404)
    variant=get_vehicle_variant(variant_id)
    if not variant:
        return JSONResponse({"ok":False,"error":"Variante técnica no encontrada"},status_code=404)
    modules=[
      ("technical specifications","Datos técnicos","Motor, potencia, cilindrada y configuración de la variante"),
      ("maintenance","Mantenimiento","Intervalos, operaciones y condiciones de servicio"),
      ("timing","Distribución","Correa/cadena, procedimientos y referencias de trabajo"),
      ("torque","Pares de apriete","Pares, ángulos y condiciones de apriete"),
      ("lubricants","Fluidos","Aceites, refrigerante, capacidades y especificaciones"),
      ("diagnosis","Diagnóstico","Síntomas, pruebas, valores y procedimientos"),
      ("drawings","Esquemas","Esquemas eléctricos y componentes"),
      ("fuses","Fusibles","Cajas, posiciones y funciones"),
      ("oem","OEM / referencias","Códigos y referencias del fabricante"),
      ("repair manuals","Reparación","Procedimientos y documentación de reparación"),
      ("engine management","Gestión motor","Sistemas de gestión y diagnóstico"),
      ("comfort electronics","Electrónica confort","Sistemas de carrocería y confort"),
      ("repair times","Tiempos reparación","Tiempos de trabajo documentados"),
      ("recalls","Recalls","Campañas y avisos documentados"),
      ("smart fix","Smart Fix / Cases","Casos y soluciones técnicas documentadas"),
      ("cost estimate","Coste estimado","Base para presupuestos; no inventa precios")
    ]
    c=db()
    tech_rows=c.execute(
        "SELECT category,COUNT(*) n FROM technical_records WHERE variant_id=? GROUP BY category",
        (variant_id,)
    ).fetchall()
    evidence_rows=c.execute(
        "SELECT category,COUNT(*) n FROM evidence WHERE variant_id=? GROUP BY category",
        (variant_id,)
    ).fetchall()
    c.close()
    tech={r["category"]:r["n"] for r in tech_rows}
    ev={r["category"]:r["n"] for r in evidence_rows}
    data=[]
    for category,label,description in modules:
        tc=int(tech.get(category,0) or 0)
        ec=int(ev.get(category,0) or 0)
        if tc:
            status="CONTRASTADO"
        elif ec:
            status="EVIDENCIA WEB"
        else:
            status="SIN DATOS LOCALES"
        data.append({
            "category":category,"label":label,"description":description,
            "status":status,"technical_count":tc,"evidence_count":ec
        })
    return {
        "ok":True,
        "variant":variant,
        "modules":data,
        "summary":{
            "verified":sum(1 for x in data if x["status"]=="CONTRASTADO"),
            "evidence":sum(1 for x in data if x["status"]=="EVIDENCIA WEB"),
            "empty":sum(1 for x in data if x["status"]=="SIN DATOS LOCALES")
        }
    }


@app.get("/api/evidence/{vehicle_id:path}")
def evidence_for_vehicle(vehicle_id:str, category:str=""):
    """Devuelve evidencia pública ya almacenada para la variante, sin lanzar una nueva búsqueda."""
    profile=find_technical_profile(vehicle_id)
    variant_id=profile.get("variant_id") or profile.get("profile_vehicle_id")
    if not variant_id:
        return []
    c=db()
    if category:
        rows=c.execute(
            """SELECT e.*, sd.title AS document_title, sd.url AS document_url,
                      sd.source_class AS document_source_class
               FROM evidence e
               LEFT JOIN source_documents sd ON sd.document_id=e.document_id
               WHERE e.variant_id=? AND e.category=?
               ORDER BY e.created_at DESC""",
            (variant_id,category)
        ).fetchall()
    else:
        rows=c.execute(
            """SELECT e.*, sd.title AS document_title, sd.url AS document_url,
                      sd.source_class AS document_source_class
               FROM evidence e
               LEFT JOIN source_documents sd ON sd.document_id=e.document_id
               WHERE e.variant_id=?
               ORDER BY e.created_at DESC""",
            (variant_id,)
        ).fetchall()
    c.close()
    return [dict(r) for r in rows]


@app.get("/api/technical/{vehicle_id:path}")
def technical(vehicle_id:str, category:str=Query("")):
    profile=find_technical_profile(vehicle_id,category)
    profile_id=profile.get("profile_vehicle_id") or vehicle_id
    c=db()
    # La variante canónica es la clave primaria lógica de los datos técnicos.
    # vehicle_id queda como fallback para registros heredados durante la migración.
    variant_id=profile.get("variant_id") or profile_id
    if category:
        rows=c.execute(
            """SELECT tr.*,
                      sd.document_id AS source_document_id,
                      sd.title AS document_title,
                      sd.url AS document_url,
                      sd.source_class AS document_source_class,
                      sd.publisher AS document_publisher,
                      sd.document_type AS document_type,
                      sd.language AS document_language,
                      sd.revision AS document_revision,
                      sd.retrieved_at AS document_retrieved_at
               FROM technical_records tr
               LEFT JOIN source_documents sd
                 ON sd.variant_id=tr.variant_id AND sd.url=tr.source_url
               WHERE (tr.variant_id=? OR (tr.variant_id IS NULL AND tr.vehicle_id=?))
                 AND tr.category=?
               ORDER BY tr.id""",
            (variant_id,profile_id,category)
        ).fetchall()
    else:
        rows=c.execute(
            """SELECT tr.*,
                      sd.document_id AS source_document_id,
                      sd.title AS document_title,
                      sd.url AS document_url,
                      sd.source_class AS document_source_class,
                      sd.publisher AS document_publisher,
                      sd.document_type AS document_type,
                      sd.language AS document_language,
                      sd.revision AS document_revision,
                      sd.retrieved_at AS document_retrieved_at
               FROM technical_records tr
               LEFT JOIN source_documents sd
                 ON sd.variant_id=tr.variant_id AND sd.url=tr.source_url
               WHERE (tr.variant_id=? OR (tr.variant_id IS NULL AND tr.vehicle_id=?))
               ORDER BY tr.category,tr.id""",
            (variant_id,profile_id)
        ).fetchall()
    c.close()
    return [dict(r) for r in rows]



@app.get("/api/variant/resolve")
def resolve_variant(
    make:str=Query(""), model:str=Query(""), year:str=Query(""),
    engine:str=Query(""), engine_code:str=Query(""), transmission:str=Query(""),
    drive:str=Query(""), fuel:str=Query(""), market:str=Query("")
):
    result=resolve_variant_signals({
        "make":make,"model":model,"year":year,"engine":engine,
        "engine_code":engine_code,"transmission":transmission,
        "drive":drive,"fuel":fuel,"market":market
    })
    if not result:
        return JSONResponse({"ok":False,"error":"No hay una variante técnica suficientemente determinada con estas señales."},status_code=404)
    return {"ok":True,"variant":result}

@app.get("/api/variant/{vehicle_id:path}")
def variant(vehicle_id:str):
    profile=find_technical_profile(vehicle_id)
    variant_id=profile.get("profile_vehicle_id")
    data=get_vehicle_variant(vehicle_id)
    if not data and variant_id:
        data=get_vehicle_variant(variant_id)
    if not data:
        return JSONResponse({"error":"Variante técnica no resuelta"},status_code=404)
    return data

@app.get("/api/evidence")
def evidence(limit:int=Query(50,ge=1,le=200)):
    c=db()
    rows=c.execute("""
      SELECT e.*,
             sd.title AS document_title,
             sd.url AS document_url,
             sd.source_class AS document_source_class,
             sd.publisher AS document_publisher,
             sd.document_type AS document_type,
             sd.language AS document_language,
             sd.revision AS document_revision,
             sd.retrieved_at AS document_retrieved_at
      FROM evidence e
      LEFT JOIN source_documents sd ON sd.document_id=e.document_id
      ORDER BY e.id DESC LIMIT ?
    """,(limit,)).fetchall()
    c.close()
    return [dict(r) for r in rows]

@app.get("/api/license")
def license_info():
    return {"dataset":"VehiclesDB","version":DATASET_VERSION,"license":"CC BY 4.0","attribution":"Vehicle data by VehiclesDB","url":"https://vehiclesdb.com"}

if __name__ == "__main__":
    init_db()
    threading.Timer(1.5, lambda: webbrowser.open("http://127.0.0.1:8000")).start()
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
