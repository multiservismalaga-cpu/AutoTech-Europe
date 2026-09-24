const $ = (s) => document.querySelector(s);
// VIN crosscheck ready
const HYUNDAI_VDS_HA811 = "H" + "A811";
const HYUNDAI_WMI_KMH = "K" + "M" + "H";

function decodeHyundaiLocal(vin) {
  if (vin.slice(0,3) !== HYUNDAI_WMI_KMH || vin.slice(3,8) !== HYUNDAI_VDS_HA811 || vin[9] !== "S") return null;
  return ["Hyundai Motor Company","Kona SX2","HEV","2025","G4LL","1.6 GDi HEV","1580","4","Automática DCT de 6 velocidades","Tracción delantera","Ulsan, Corea del Sur"];
}
let selected = null;
let makesCache = [];

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (m) => ({
    "&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"
  }[m]));
}

async function api(url, opt) {
  const r = await fetch(url, opt);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

async function status() {
  const info = $("#dbinfo");
  if (!info) return;
  try {
    const s = await api("/api/status?ts=" + Date.now());
    const n = Number(s.count || 0).toLocaleString("es-ES");
    if (s.sync === "descargando" || s.sync === "comprobando" || s.sync === "iniciando") {
      info.textContent = "Preparando base abierta… " + n + " vehículos · " + s.sync;
      setTimeout(status, 1500);
    } else if (s.sync === "error") {
      info.textContent = "Base local: " + n + " vehículos · error: " + (s.error || "no se pudo actualizar");
    } else {
      info.textContent = "Base: " + n + " vehículos · " + (s.dataset || "VehiclesDB") + " · CC BY 4.0";
    }
  } catch (e) {
    info.textContent = "Conectando con la base local…";
    setTimeout(status, 2000);
  }
}

function showStatus(text) {
  if ($("#status")) $("#status").textContent = text;
}

function render(list) {
  const box = $("#results");
  if (!box) return;
  if (!list.length) {
  box.innerHTML = '<div class="card"><b>No hay coincidencias.</b><p>Prueba otra búsqueda o utiliza evidencia pública.</p></div>';
    return;
  }
  box.innerHTML = list.map((v) =>
    '<article class="vehicle"><span class="badge">' + esc(v.kind || "vehicle") +
    '</span><h3>' + esc(v.make) + " " + esc(v.model) + '</h3><p>' +
    esc(v.years && v.years !== "[]" ? v.years : "Identidad de modelo") +
    '</p><button class="secondary" data-id="' + esc(v.id) + '">Abrir ficha</button></article>'
  ).join("");
  document.querySelectorAll("[data-id]").forEach((b) => {
    b.onclick = () => openVehicle(b.dataset.id);
  });
  $("#results").scrollIntoView({behavior:"smooth",block:"start"});
}

async function find() {
  const q = $("#q").value.trim();
  if (!q) {
    showStatus("Escribe una marca, modelo, código de motor o referencia.");
    return;
  }
  showStatus("Buscando en la base local…");
  try {
    const r = await api("/api/vehicles?q=" + encodeURIComponent(q));
    render(r);
    if (r.length) {
      showStatus(r.length + " coincidencias en la base local");
      return;
    }
    showStatus("Sin coincidencia local; contrastando fuentes públicas…");
    await research(q);
    showStatus("Sin coincidencia exacta en la base. Se muestran fuentes públicas para contrastar.");
  } catch (e) {
    showStatus("Error: " + e.message);
  }
}

async function loadMakes() {
  const select = $("#makeSelect");
  if (!select) return;
  try {
    const data = await api("/api/makes");
    makesCache = data;
    select.innerHTML = '<option value="">Selecciona marca</option>' +
      data.map(x => '<option value="' + esc(x.make) + '">' + esc(x.make) + ' (' + Number(x.count).toLocaleString("es-ES") + ')</option>').join("");
  } catch (e) {
    select.innerHTML = '<option value="">No se pudieron cargar las marcas</option>';
  }
}

async function loadModels(make, q="") {
  const select = $("#modelSelect");
  if (!select) return;
  select.disabled = true;
  select.innerHTML = '<option value="">Cargando modelos…</option>';
  if (!make) {
    select.innerHTML = '<option value="">Selecciona una marca</option>';
    return;
  }
  try {
    const data = await api("/api/models?make=" + encodeURIComponent(make) + "&q=" + encodeURIComponent(q) + "&limit=500");
    select.innerHTML = '<option value="">Todos los modelos</option>' +
      data.map(x => '<option value="' + esc(x.model) + '" data-id="' + esc(x.id) + '">' + esc(x.model) + '</option>').join("");
    select.disabled = false;
  } catch (e) {
    select.innerHTML = '<option value="">No se pudieron cargar los modelos</option>';
  }
}

async function searchCatalog() {
  const make = $("#makeSelect").value.trim();
  const model = $("#modelSelect").value.trim();
  const filter = $("#modelFilter").value.trim();
  const engine = $("#catalogEngine").value.trim();
  if (!make) {
    showStatus("Selecciona una marca.");
    return;
  }
  showStatus("Buscando vehículo exacto en el catálogo…");
  try {
    let results = [];
    if (engine) {
      results = await api("/api/engine-search?q=" + encodeURIComponent(engine));
      results = results.filter(v => !make || v.make.toLowerCase() === make.toLowerCase());
      if (model) results = results.filter(v => v.model.toLowerCase() === model.toLowerCase());
    }
    if (!results.length) {
      const q = [make, model || filter].filter(Boolean).join(" ");
      results = await api("/api/vehicles?q=" + encodeURIComponent(q));
    }
    render(results);
    showStatus(results.length
      ? results.length + " coincidencias en la base local"
      : "No hay coincidencia local con esos criterios.");
  } catch (e) {
    showStatus("Error: " + e.message);
  }
}

async function searchEngine() {
  const engine = $("#engineHome").value.trim();
  if (!engine) {
    showStatus("Introduce un código o familia de motor.");
    return;
  }
  showStatus("Buscando código de motor en la base local…");
  try {
    const local = await api("/api/engine-search?q=" + encodeURIComponent(engine));
    if (local.length) {
      render(local);
      showStatus(local.length + " coincidencias relacionadas con " + engine);
      return;
    }
    showStatus("No hay coincidencia local; buscando evidencia pública del motor…");
    await runResearch({
      vehicle_id:null,
      make:"",
      model:"",
      engine,
      category:"technical specifications"
    });
    showStatus("Sin coincidencia local. La evidencia web queda pendiente de contraste.");
  } catch (e) {
    showStatus("Error: " + e.message);
  }
}

function renderVinDecode(data) {
  const box = $("#vinDecode");
  if (!box) return;
  box.classList.remove("hidden");
  if (!data.ok) {
    box.innerHTML =
      '<div class="eyebrow">DECODIFICACIÓN VIN</div><h3>No se pudo completar la decodificación</h3>' +
      '<p>' + esc(data.error || "El servicio público no devolvió datos.") + '</p>' +
      '<p class="small">VIN: ' + esc(data.vin || "") + ' · WMI: ' + esc(data.wmi || "") +
      ' · Código de año: ' + esc(data.year_code || "") + '</p>';
    box.scrollIntoView({behavior:"smooth"});
    return;
  }
  const hx = decodeHyundaiLocal(data.vin);
  const rows = (data.results || []).map(x =>
    '<div class="vin-row"><b>' + esc(x.field) + '</b><span>' + esc(x.value) + '</span></div>'
  ).join("");
  let hxHtml = hx ? "<section class=\"vin-crosscheck\"><div class=\"eyebrow\">CRUCE VIN</div><h3>" + esc(hx[0]) + " " + esc(hx[1]) + " " + esc(hx[2]) + "</h3><p>" + esc(hx[3]) + " | " + esc(hx[4]) + " | " + esc(hx[5]) + " | " + esc(hx[6]) + " cc</p><p>" + esc(hx[8]) + " | " + esc(hx[9]) + " | " + esc(hx[10]) + "</p></section>" : "";
  box.innerHTML =
    '<div class="detail-nav"><div class="nav-crumb">Identificación VIN</div><button id="closeVin" class="secondary">Cerrar</button></div>' +
    '<div class="eyebrow">IDENTIFICACIÓN POR VIN</div><h3>VIN ' + esc(data.vin) + '</h3>' + hxHtml +
    '<p class="small">Fuente: ' + esc(data.source) + ' · ' + esc(data.confidence) +
    '. Esta información sirve para orientar la identificación y no sustituye una fuente OEM o una base VIN europea licenciada.</p>' +
    '<div class="vin-meta"><span>WMI: <b>' + esc(data.wmi) + '</b></span><span>VDS: <b>' + esc(data.vds) +
    '</b></span><span>VIS: <b>' + esc(data.vis) + '</b></span><span>Código año: <b>' +
    esc(data.year_code) + '</b></span></div>' +
    '<div class="vin-grid">' + (rows || '<p>No hay campos públicos útiles para este VIN.</p>') + '</div>';
  $("#closeVin").onclick = () => box.classList.add("hidden");
  box.scrollIntoView({behavior:"smooth"});
  const make = (data.results || []).find(x => x.field === "Marca")?.value || "";
  const model = (data.results || []).find(x => x.field === "Modelo")?.value || "";
  if (make || model) {
    setTimeout(async () => {
      try {
        const q = [make, model].filter(Boolean).join(" ");
        const local = await api("/api/vehicles?q=" + encodeURIComponent(q));
        if (local.length) {
          render(local);
          showStatus(local.length + " coincidencias locales relacionadas con el VIN decodificado");
        }
      } catch (e) {}
    }, 0);
  }
}

async function decodeVin() {
  const vin = $("#vinHome").value.trim().toUpperCase().replace(/[ -]/g,"");
  if (!/^[A-HJ-NPR-Z0-9]{17}$/.test(vin)) {
    showStatus("El VIN debe tener 17 caracteres válidos, sin I, O ni Q.");
    return;
  }
  showStatus("Decodificando VIN con fuente pública…");
  try {
    const data = await api("/api/vin/decode/" + encodeURIComponent(vin));
    renderVinDecode(data);
    showStatus(data.ok ? "VIN decodificado. Revisa la fuente y contrasta la variante exacta." : "VIN recibido; no hubo datos públicos suficientes.");
  } catch (e) {
    showStatus("Error al decodificar VIN: " + e.message);
  }
}

async function saveHomeVin() {
  const vin = $("#vinHome").value.trim().toUpperCase().replace(/[ -]/g,"");
  if (!/^[A-HJ-NPR-Z0-9]{17}$/.test(vin)) {
    showStatus("El VIN debe tener 17 caracteres válidos, sin I, O ni Q.");
    return;
  }
  try {
    const r = await api("/api/vin", {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({vin,vehicle_id:selected?.id || null})
    });
    showStatus(r.ok ? "VIN guardado en la base local." : "No se pudo guardar el VIN.");
  } catch (e) {
    showStatus("No se pudo guardar el VIN.");
  }
}

const modules = [
  ["Mantenimiento","maintenance","Intervalos, operaciones y condiciones de servicio"],
  ["Distribución","timing","Correa/cadena, procedimientos y referencias de trabajo"],
  ["Pares de apriete","torque","Pares, ángulos y condiciones de apriete"],
  ["Fluidos","lubricants","Aceites, refrigerante, capacidades y especificaciones"],
  ["Diagnóstico","diagnosis","Síntomas, pruebas, valores y procedimientos"],
  ["Esquemas","drawings","Esquemas eléctricos y componentes"],
  ["Fusibles","fuses","Cajas, posiciones y funciones"],
  ["OEM / referencias","oem","Códigos y referencias del fabricante"],
  ["Reparación","repair manuals","Procedimientos y documentación de reparación"],
  ["Gestión motor","engine management","Sistemas de gestión y diagnóstico"],
  ["Electrónica confort","comfort electronics","Sistemas de carrocería y confort"],
  ["Tiempos reparación","repair times","Tiempos de trabajo documentados"],
  ["Recalls","recalls","Campañas y avisos documentados"],
  ["Smart Fix / Cases","smart fix","Casos y soluciones técnicas documentadas"],
  ["Coste estimado","cost estimate","Base para presupuestos; no inventa precios"]
];

async function openVehicle(id) {
  selected = await api("/api/vehicle/" + encodeURIComponent(id));
  $("#detail").classList.remove("hidden");
  const mods = modules.map((m) =>
    '<article class="module"><div><b>' + esc(m[0]) + '</b><span>' + esc(m[2]) +
    '</span></div><button class="secondary module-btn" data-category="' + esc(m[1]) +
    '">Buscar evidencia</button></article>'
  ).join("");
  $("#detail").innerHTML =
    '<div class="detail-nav"><button id="backResults" class="secondary">← Volver a resultados</button>' +
    '<div class="nav-crumb">Inicio › Resultados › ' + esc(selected.make) + " " + esc(selected.model) +
    '</div><button id="goTop" class="secondary">↑ Arriba</button></div>' +
    '<div class="eyebrow">FICHA DE IDENTIFICACIÓN</div><h2>' + esc(selected.make) + " " +
    esc(selected.model) + '</h2><p>Fuente de identidad: VehiclesDB · ID: ' + esc(selected.id) +
    '</p><div class="grid"><div class="metric"><b>Años</b><span>' + esc(selected.years || "—") +
    '</span></div><div class="metric"><b>Carrocería</b><span>' + esc(selected.body_types || "—") +
    '</span></div><div class="metric"><b>Disponibilidad</b><span>' + esc(selected.availability || "—") +
    '</span></div><div class="metric"><b>Fuente</b><span>VehiclesDB</span></div></div>' +
    '<div class="card"><label for="vin">VIN / bastidor</label><input id="vin" maxlength="17" placeholder="17 caracteres">' +
    '<div class="actions"><button id="savevin">Guardar VIN</button><button id="decodeDetailVin" class="secondary">Decodificar VIN</button></div>' +
    '<p id="vinstatus" class="small">Se guarda como identificador técnico del vehículo. No se solicita ningún dato del propietario.</p></div>' +
    '<section class="technical"><div class="section-head"><div><div class="eyebrow">MÓDULOS TÉCNICOS</div>' +
    '<h3>Ficha de trabajo</h3></div><span class="status-chip">Estructura preparada · datos por contrastar</span></div>' +
    '<p class="small intro">La estructura sigue el flujo profesional de identificación → vehículo exacto → módulo técnico. ' +
    'Los módulos todavía no presentan valores técnicos inventados: cada búsqueda abre evidencia pública que debe contrastarse.</p>' +
    '<div class="modules">' + mods + '</div></section><button id="research" class="secondary">Investigar fuentes técnicas generales</button>';
  $("#research").onclick = () => research();
  $("#savevin").onclick = saveVin;
  $("#decodeDetailVin").onclick = async () => {
    const vin = $("#vin").value.trim().toUpperCase().replace(/[ -]/g,"");
    if (!/^[A-HJ-NPR-Z0-9]{17}$/.test(vin)) { $("#vinstatus").textContent="VIN no válido."; return; }
    $("#vinstatus").textContent="Decodificando VIN…";
    const data = await api("/api/vin/decode/" + encodeURIComponent(vin));
    renderVinDecode(data);
    $("#vinstatus").textContent = data.ok ? "Decodificación pública realizada; contrasta la variante exacta." : "No hubo datos públicos suficientes.";
  };
  $("#backResults").onclick = backToResults;
  $("#goTop").onclick = () => window.scrollTo({top:0,behavior:"smooth"});
  document.querySelectorAll(".module-btn").forEach((b) => {
    b.onclick = () => researchCategory(b.dataset.category);
  });
  $("#detail").scrollIntoView({behavior:"smooth"});
}

function backToResults() {
  $("#detail").classList.add("hidden");
  $("#webresults").classList.add("hidden");
  showStatus("Resultados disponibles.");
  $("#results").scrollIntoView({behavior:"smooth"});
}

async function saveVin() {
  const vin = $("#vin").value.trim();
  $("#vinstatus").textContent = "Guardando…";
  try {
    const r = await api("/api/vin", {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({vin, vehicle_id:selected?.id || null})
    });
    $("#vinstatus").textContent = r.ok ? "VIN guardado para este vehículo." : "No se pudo guardar el VIN.";
  } catch (e) {
    $("#vinstatus").textContent = "VIN no válido o no se pudo guardar.";
  }
}

async function researchCategory(category) {
  const base = selected || {};
  if (!base.id) {
    showStatus("Selecciona primero un vehículo.");
    return;
  }
  try {
    const rows = await api("/api/technical/" + encodeURIComponent(base.id) +
      "?category=" + encodeURIComponent(category));
    if (rows.length) {
      renderTechnicalRows(category, rows);
      return;
    }
  } catch (e) {}
  await runResearch({vehicle_id:base.id,make:base.make,model:base.model,category});
}

function renderTechnicalRows(category, rows) {
  $("#webresults").classList.remove("hidden");
  $("#webresults").innerHTML =
    '<div class="module-nav"><button id="backDetail" class="secondary">← Volver a ficha</button>' +
    '<div class="nav-crumb">Vehículo seleccionado / módulo técnico</div><button id="moduleTop" class="secondary">↑ Arriba</button></div>' +
    '<div class="eyebrow">DATOS TÉCNICOS CONTRASTADOS</div><h3>' +
    esc(category.replace(/\b\w/g, (c) => c.toUpperCase())) +
    '</h3><p class="small">Solo se muestran datos asociados a una variante y fuente concreta. No se mezclan versiones.</p>' +
    rows.map((x) =>
      '<div class="tech-row"><div><b>' + esc(x.field) + '</b><span>' + esc(x.value) +
      (x.unit ? ' ' + esc(x.unit) : '') + '</span></div><div class="small"><a class="source" target="_blank" rel="noopener" href="' +
      esc(x.source_url) + '">' + esc(x.source_title) + '</a><br>' + esc(x.source_class) + ' · ' +
      esc(x.confidence) + ' · ' + esc(x.applicable_from || '') + '–' + esc(x.applicable_to || '') +
      '</div></div>'
    ).join("");
  $("#backDetail").onclick = () => {
    $("#webresults").classList.add("hidden");
    $("#detail").scrollIntoView({behavior:"smooth"});
  };
  $("#moduleTop").onclick = () => window.scrollTo({top:0,behavior:"smooth"});
}

async function runResearch(payload) {
  $("#webresults").classList.remove("hidden");
  $("#webresults").innerHTML = "<b>Buscando evidencia pública…</b>";
  try {
    const r = await api("/api/research", {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify(payload)
    });
    if (!r.ok) throw new Error(r.error);
    const title = payload.category
      ? payload.category.replace(/\b\w/g, (c) => c.toUpperCase())
      : "Evidencia técnica";
    const results = (r.results || []).map((x) =>
      '<div class="evidence"><a class="source" target="_blank" rel="noopener" href="' +
      esc(x.url) + '">' + esc(x.title || x.url) + '</a><div class="small">' +
      esc(x.source_class) + ' · ' + esc(x.confidence) + '</div><p class="small">' +
      esc(x.snippet || "Sin extracto") + '</p></div>'
    ).join("");
    $("#webresults").innerHTML =
      '<div class="module-nav"><button id="backDetail" class="secondary">← Volver a ficha</button>' +
      '<div class="nav-crumb">Vehículo seleccionado / módulo técnico</div><button id="moduleTop" class="secondary">↑ Arriba</button></div>' +
      '<div class="eyebrow">EVIDENCIA WEB</div><h3>' + esc(r.query || "") + '</h3><p class="small">' +
      'Módulo: ' + esc(title) + ' · Los resultados son evidencia para contraste, no sustituyen documentación OEM o datos licenciados.</p>' +
      (results || "<p>No se encontraron resultados públicos.</p>");
    $("#backDetail").onclick = () => {
      $("#webresults").classList.add("hidden");
      $("#detail").scrollIntoView({behavior:"smooth"});
    };
    $("#moduleTop").onclick = () => window.scrollTo({top:0,behavior:"smooth"});
  } catch (e) {
    $("#webresults").innerHTML = "<b>No se pudo consultar Internet.</b><p>" + esc(e.message) + "</p>";
  }
}

async function research(qOverride) {
  const q = (qOverride || $("#q").value).trim();
  if (!selected && !q) return;
  const base = selected || {};
  await runResearch({
    vehicle_id:base.id || null,
    make:base.make || q,
    model:base.model || "",
    category:"technical specifications"
  });
}

function initIdentification() {
  document.querySelectorAll(".id-tab").forEach(tab => {
    tab.onclick = () => {
      document.querySelectorAll(".id-tab").forEach(x => x.classList.remove("active"));
      document.querySelectorAll(".id-panel").forEach(x => x.classList.add("hidden"));
      tab.classList.add("active");
      const panel = document.querySelector('[data-panel="' + tab.dataset.idtab + '"]');
      if (panel) panel.classList.remove("hidden");
    };
  });
  loadMakes();
  $("#makeSelect").onchange = () => loadModels($("#makeSelect").value, $("#modelFilter").value.trim());
  $("#modelFilter").oninput = () => {
    if ($("#makeSelect").value) loadModels($("#makeSelect").value, $("#modelFilter").value.trim());
  };
  $("#searchCatalog").onclick = searchCatalog;
  $("#searchEngine").onclick = searchEngine;
  $("#decodeVin").onclick = decodeVin;
  $("#saveHomeVin").onclick = saveHomeVin;
  $("#vinHome").addEventListener("keydown", e => { if (e.key === "Enter") decodeVin(); });
  $("#engineHome").addEventListener("keydown", e => { if (e.key === "Enter") searchEngine(); });
  $("#q").addEventListener("keydown", e => { if (e.key === "Enter") find(); });
}

function init() {
  $("#find").onclick = find;
  $("#internet").onclick = research;
  $("#refresh").onclick = async () => {
    if (!confirm("¿Actualizar la base abierta?")) return;
    showStatus("Actualizando…");
    try {
      const r = await api("/api/database/refresh", {method:"POST"});
      showStatus(r.ok
        ? "Base actualizada: " + Number(r.count).toLocaleString("es-ES") + " vehículos"
        : "No se pudo actualizar: " + r.error);
      await status();
      await find();
    } catch (e) {
      showStatus("Error: " + e.message);
    }
  };
  initIdentification();
  status();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
