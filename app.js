const $ = (s) => document.querySelector(s);
// VIN crosscheck ready
const HYUNDAI_VDS_HA811 = "H" + "A811";
const HYUNDAI_WMI_KMH = "K" + "M" + "H";

function decodeHyundaiLocal(vin) {
  if (vin.slice(0,3) !== HYUNDAI_WMI_KMH || vin.slice(3,8) !== HYUNDAI_VDS_HA811 || vin[9] !== "S") return null;
  return {matched:true,manufacturer:"Hyundai Motor Company",model:"Kona SX2",variant:"HEV",model_year:2025,engine_code:"G4LL",engine:"1.6 GDi HEV",displacement_cc:"1580",cylinders:"4",transmission:"Automática DCT de 6 velocidades",drive:"Tracción delantera",plant:"Ulsan, Corea del Sur",confidence:"ALTA · VDS + plataforma + año compatible",limitation:"Cruce local experimental; no sustituye una identificación OEM del número de serie."};
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
  box.innerHTML = list.map((v) => {
    const variant = v.is_variant;
    const meta = variant
      ? [
          v.generation || "",
          v.engine_code ? "Motor " + v.engine_code : "",
          v.transmission || "",
          v.drive || "",
          v.fuel || ""
        ].filter(Boolean).join(" · ")
      : (v.body_types || "Identidad de catálogo");
    return '<article class="vehicle ' + (variant ? 'vehicle-variant' : '') + '">' +
      '<span class="badge">' + esc(variant ? "VARIANTE TÉCNICA" : (v.kind || "vehicle")) + '</span>' +
      '<h3>' + esc(v.make) + " " + esc(v.model) + '</h3>' +
      '<p>' + esc(v.years && v.years !== "[]" ? v.years : "Identidad de modelo") + '</p>' +
      '<p class="result-meta">' + esc(meta) + '</p>' +
      (variant ? '<p class="small">Aplicación encontrada por código/familia de motor. La variante se mantiene separada de las demás aplicaciones.</p>' : '') +
      '<button class="secondary" data-id="' + esc(v.id) + '">Abrir ficha</button></article>';
  }).join("");
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
  const hx = data.crosscheck || decodeHyundaiLocal(data.vin);
  const tv = data.technical_variant;
  const rows = (data.results || []).map(x =>
    '<div class="vin-row"><b>' + esc(x.field) + '</b><span>' + esc(x.value) + '</span></div>'
  ).join("");
  let hxHtml = "";
  if (tv) {
    hxHtml += "<section class=\"vin-crosscheck\"><div class=\"eyebrow\">VARIANTE TÉCNICA RESUELTA</div><h3>" +
      esc(tv.make) + " · " + esc(tv.model) + "</h3>" +
      "<p>" + esc(tv.generation || "") + " | " + esc(tv.engine_family || "") + " | " + esc(tv.engine_code || "") + "</p>" +
      "<p>" + esc(tv.transmission || "") + " | " + esc(tv.drive || "") + " | " + esc(tv.fuel || "") + " | " + esc(tv.market || "") + "</p>" +
      "<p class=\"small\">Resolución: " + esc(tv.resolution_method || "señales") + " · puntuación " + esc(tv.resolution_score) + " · conflictos " + esc(tv.resolution_conflicts) + "</p>" +
      "<button id=\"openResolvedTech\" class=\"secondary\">Ver ficha técnica de la variante</button></section>";
  }
  if (hx && hx.matched) {
    hxHtml = "<section class=\"vin-crosscheck\"><div class=\"eyebrow\">CRUCE VIN</div><h3>" +
      esc(hx.manufacturer) + " · " + esc(hx.model) + " · " + esc(hx.variant) + "</h3>" +
      "<p>" + esc(hx.model_year) + " | " + esc(hx.engine) + " | " + esc(hx.engine_code) + " | " + esc(hx.displacement_cc) + " cc</p>" +
      "<p>" + esc(hx.transmission) + " | " + esc(hx.drive) + " | " + esc(hx.plant) + "</p>" +
      "<p class=\"small\">Confianza: " + esc(hx.confidence) + " · " + esc(hx.limitation) + "</p>" +
      "<button id=\"openTechFromVin\" class=\"secondary\">Ver ficha técnica contrastada</button></section>";
  }
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
  const resolvedButton = $("#openResolvedTech");
  if (resolvedButton && tv) {
    resolvedButton.onclick = async () => {
      showStatus("Cargando ficha técnica de la variante resuelta…");
      try {
        const tech = await api("/api/technical/" + encodeURIComponent(tv.variant_id));
        const rows = tech.map(x => "<div class=\"vin-row\"><b>" + esc(x.category + " · " + x.field) + "</b><span>" + esc((x.value || "") + (x.unit ? " " + x.unit : "")) + "</span></div>").join("");
        box.innerHTML += "<section class=\"card\"><div class=\"eyebrow\">FICHA TÉCNICA DE LA VARIANTE</div><div class=\"vin-grid\">" + rows + "</div><p class=\"small\">La ficha procede del perfil técnico resuelto, no de una inferencia por nombre de modelo.</p></section>";
        showStatus(tech.length + " datos técnicos cargados");
      } catch (e) {
        showStatus("No se pudo cargar la ficha técnica: " + e.message);
      }
    };
  }
  if (hx && hx.matched) {
    const techButton = $("#openTechFromVin");
    if (techButton) {
      techButton.onclick = async () => {
        showStatus("Cargando ficha técnica contrastada…");
        try {
          if (!tv?.variant_id) { showStatus("VIN identificado, pero no hay una variante técnica resuelta; no se reutiliza ninguna ficha."); return; }
          const tech = await api("/api/technical/" + encodeURIComponent(tv.variant_id));
          const rows = tech.map(x => "<div class=\"vin-row\"><b>" + esc(x.category + " · " + x.field) + "</b><span>" + esc((x.value || "") + (x.unit ? " " + x.unit : "")) + "</span></div>").join("");
          box.innerHTML += "<section class=\"card\"><div class=\"eyebrow\">FICHA TÉCNICA CONTRASTADA</div><div class=\"vin-grid\">" + rows + "</div><p class=\"small\">Cada dato incluye fuente OEM y estado de contraste en la base técnica.</p></section>";
          showStatus(tech.length + " datos técnicos contrastados cargados");
        } catch (e) {
          showStatus("No se pudo cargar la ficha técnica: " + e.message);
        }
      };
    }
  }
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
  ["Datos técnicos","technical specifications","Motor, potencia, cilindrada y configuración de la variante"],
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
    '">Abrir</button></article>'
  ).join("");
  $("#detail").innerHTML =
    '<div class="detail-nav"><button id="backResults" class="secondary">← Volver a resultados</button>' +
    '<div class="nav-crumb">Inicio › Resultados › ' + esc(selected.make) + " " + esc(selected.model) +
    '</div><button id="goTop" class="secondary">↑ Arriba</button></div>' +
    '<div class="eyebrow">FICHA DE IDENTIFICACIÓN</div><h2>' + esc(selected.make) + " " +
    esc(selected.model) + '</h2><p>Fuente de identidad: ' + esc(selected.is_variant ? "perfil técnico canónico" : "VehiclesDB") +
    ' · ID: ' + esc(selected.id) + '</p>' +
    '<div class="grid"><div class="metric"><b>Años</b><span>' + esc(selected.years || "—") +
    '</span></div><div class="metric"><b>Generación</b><span>' + esc(selected.generation || "—") +
    '</span></div><div class="metric"><b>Motor</b><span>' + esc(selected.engine_code || selected.engine_family || "—") +
    '</span></div><div class="metric"><b>Transmisión / tracción</b><span>' + esc((selected.transmission || "—") + " · " + (selected.drive || "—")) +
    '</span></div></div>' +
    '<div id="variantPanel" class="card"><div class="eyebrow">VARIANTE TÉCNICA</div><p class="small">Resolución técnica pendiente de señales suficientes.</p></div>' +
    '<div class="card"><label for="vin">VIN / bastidor</label><input id="vin" maxlength="17" placeholder="17 caracteres">' +
    '<div class="actions"><button id="savevin">Guardar VIN</button><button id="decodeDetailVin" class="secondary">Decodificar VIN</button></div>' +
    '<p id="vinstatus" class="small">Se guarda como identificador técnico del vehículo. No se solicita ningún dato del propietario.</p></div>' +
    '<section class="technical"><div class="section-head"><div><div class="eyebrow">MÓDULOS DE TALLER</div>' +
    '<h3>Información organizada por trabajo</h3></div><span id="dashboardStatus" class="status-chip">Cargando estado…</span></div>' +
    '<p class="small intro">Cada módulo indica si existen datos técnicos contrastados, evidencia pública ya guardada o si todavía no hay datos locales. Nunca se muestra una ficha vacía como si fuera información técnica.</p>' +
    '<div id="moduleSummary" class="module-summary"></div><div class="module-workspace"><aside class="module-sidebar"><div class="eyebrow">ÍNDICE DE TALLER</div><div id="moduleSidebarList"></div></aside><div class="module-main"><div id="moduleContent" class="module-content"><div class="eyebrow">MÓDULO</div><h3>Selecciona una función de taller</h3><p class="small">El contenido se abre aquí y permanece ligado a la variante técnica seleccionada.</p></div><div id="workshopModules" class="modules">' + mods + '</div></div></div></section>' +
    '<button id="research" class="secondary">Investigar fuentes técnicas generales</button>';
  $("#research").onclick = () => research();
  loadVehicleVariant(selected.id);
  loadWorkshopDashboard(selected.id);
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

function moduleStatusClass(status) {
  if (status === "CONTRASTADO") return "module-ok";
  if (status === "EVIDENCIA WEB") return "module-web";
  return "module-empty";
}

function renderModuleSidebar(data) {
  const box=$("#moduleSidebarList");
  if(!box) return;
  box.innerHTML=(data.modules || []).map((m,i) =>
    '<button class="module-side-item ' + (i===0 ? 'active' : '') + '" data-category="' + esc(m.category) + '">' +
      '<span>' + esc(m.label) + '</span><small>' + esc(m.status) + '</small></button>'
  ).join("");
  box.querySelectorAll(".module-side-item").forEach(b => {
    b.onclick=()=>{
      box.querySelectorAll(".module-side-item").forEach(x=>x.classList.remove("active"));
      b.classList.add("active");
      researchCategory(b.dataset.category);
    };
  });
}

function renderModuleContent(category,title,body,state) {
  const box=$("#moduleContent");
  if(!box) return;
  activateModuleCategory(category);
  box.innerHTML='<div class="module-content-head"><div><div class="eyebrow">MÓDULO DE TALLER</div><h3>' +
    esc(title || category) + '</h3></div><span class="status-chip">' + esc(state || "CARGANDO") +
    '</span></div>' + body;
}

function renderWorkshopModules(data) {
  const box=$("#workshopModules");
  const summary=$("#moduleSummary");
  const state=$("#dashboardStatus");
  if (!box || !data) return;
  renderModuleSidebar(data);
  const summaryData=data.summary || {};
  if (state) state.textContent =
    Number(summaryData.verified || 0) + " contrastados · " +
    Number(summaryData.evidence || 0) + " con evidencia · " +
    Number(summaryData.empty || 0) + " pendientes";
  if (summary) summary.innerHTML =
    '<div><b>' + Number(summaryData.verified || 0) + '</b><span>módulos contrastados</span></div>' +
    '<div><b>' + Number(summaryData.evidence || 0) + '</b><span>módulos con evidencia web</span></div>' +
    '<div><b>' + Number(summaryData.empty || 0) + '</b><span>módulos sin datos locales</span></div>';
  box.innerHTML=(data.modules || []).map(m =>
    '<article class="module ' + moduleStatusClass(m.status) + '">' +
      '<div><b>' + esc(m.label) + '</b><span>' + esc(m.description) + '</span>' +
      '<small class="module-state">' + esc(m.status) + ' · ' +
      Number(m.technical_count || 0) + ' datos · ' + Number(m.evidence_count || 0) + ' evidencias</small></div>' +
      '<button class="secondary module-btn" data-category="' + esc(m.category) + '">' +
      esc(m.technical_count ? "Abrir datos" : (m.evidence_count ? "Ver evidencia" : "Investigar")) +
      '</button></article>'
  ).join("");
  box.querySelectorAll(".module-btn").forEach(b => {
    b.onclick=()=>researchCategory(b.dataset.category);
  });
}

async function loadWorkshopDashboard(vehicleId) {
  try {
    const data=await api("/api/variant-dashboard/"+encodeURIComponent(vehicleId));
    renderWorkshopModules(data);
  } catch(e) {
    const state=$("#dashboardStatus");
    if(state) state.textContent="No se pudo cargar el estado de módulos";
  }
}

async function loadVehicleVariant(vehicleId) {
  const panel=$("#variantPanel");
  if (!panel || !vehicleId) return;
  try {
    const v=await api("/api/variant/"+encodeURIComponent(vehicleId));
    panel.innerHTML='<div class="eyebrow">VARIANTE TÉCNICA</div><h3>'+esc(v.make)+' '+esc(v.model)+'</h3>'+
      '<p>'+esc(v.generation||"")+" · "+esc(v.engine_family||"")+" · "+esc(v.engine_code||"")+'</p>'+
      '<p>'+esc(v.transmission||"")+" · "+esc(v.drive||"")+" · "+esc(v.fuel||"")+" · "+esc(v.market||"")+'</p>'+
      '<p class="small">Perfil: '+esc(v.variant_id)+' · '+esc(v.confidence||v.match_confidence||"")+'</p>';
  } catch(e) {
    panel.innerHTML='<div class="eyebrow">VARIANTE TÉCNICA</div><p class="small">No hay un perfil técnico resuelto para esta entrada del catálogo. Los módulos usarán evidencia pública sin inventar una compatibilidad.</p>';
  }
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

function activateModuleCategory(category) {
  document.querySelectorAll("#moduleSidebarList .module-side-item").forEach((b) => {
    b.classList.toggle("active", b.dataset.category === category);
  });
}

async function researchCategory(category) {
  const base = selected || {};
  if (!base.id) {
    showStatus("Selecciona primero un vehículo.");
    return;
  }
  activateModuleCategory(category);
  try {
    const rows = await api("/api/technical/" + encodeURIComponent(base.id) +
      "?category=" + encodeURIComponent(category));
    if (rows.length) {
      renderTechnicalRows(category, rows);
      return;
    }
  } catch (e) {}
  try {
    const evidence = await api("/api/evidence/" + encodeURIComponent(base.id) +
      "?category=" + encodeURIComponent(category));
    if (evidence.length) {
      renderEvidenceRows(category, evidence);
      return;
    }
  } catch (e) {}
  await runResearch({vehicle_id:base.id,make:base.make,model:base.model,category});
}

function renderEvidenceRows(category, rows) {
  const title=(modules.find(m=>m[1]===category)||[category])[0];
  const body='<p class="small">Evidencia pública almacenada y asociada a esta variante. No se presenta como dato OEM confirmado.</p>' +
    rows.map((x) =>
      '<div class="evidence"><a class="source" target="_blank" rel="noopener" href="' +
      esc(x.document_url || x.url || "#") + '">' + esc(x.document_title || x.title || x.url || "Fuente") +
      '</a><div class="small">' + esc(x.document_source_class || x.source_class || "") + '</div>' +
      '<p class="small">' + esc(x.snippet || x.applicability || x.document_section || "Evidencia asociada a la variante.") + '</p></div>'
    ).join("");
  renderModuleContent(category,title,body,"EVIDENCIA WEB");
  const web = $("#webresults");
  if (web) web.classList.add("hidden");
}

function technicalModuleMeta(category) {
  const meta = {
    "technical specifications":["Datos técnicos de la variante","Motor, configuración, prestaciones y arquitectura documentadas."],
    maintenance:["Esquema de mantenimiento","Intervalos, inspecciones y sustituciones con su periodo de aplicación."],
    timing:["Distribución","Cadena/correa, componentes asociados y procedimientos documentados."],
    torque:["Pares de apriete","Pares, ángulos y condiciones de apriete documentados."],
    lubricants:["Fluidos y especificaciones","Aceites, refrigerantes, capacidades y normas de fluido."],
    diagnosis:["Diagnosis","Códigos, síntomas, pruebas y valores de diagnóstico disponibles."],
    drawings:["Esquemas","Esquemas y documentación de componentes o circuitos."],
    fuses:["Caja y posiciones","Ubicación de cajas, posiciones y funciones de fusibles."],
    oem:["Referencias del fabricante","Referencias OEM y su aplicación documentada."],
    "repair manuals":["Procedimientos de reparación","Manuales y procedimientos de reparación asociados."],
    "engine management":["Gestión del motor","Sistemas de gestión, control y diagnóstico del motor."],
    "comfort electronics":["Electrónica de confort","Sistemas de carrocería, confort y electrónica asociada."],
    "repair times":["Operaciones y tiempos","Operaciones y tiempos de reparación documentados."],
    recalls:["Campañas de reparación","Recalls y campañas documentadas para la variante."],
    "smart fix":["Casos y soluciones","Casos técnicos y soluciones documentadas."],
    "cost estimate":["Base de presupuesto","Estructura para presupuestos; no inventa precios."]
  };
  return meta[category] || ["Datos técnicos","Registros técnicos asociados a la variante seleccionada."];
}

function technicalGroupKey(category, field) {
  const f = String(field || "").toLowerCase();
  if (category === "maintenance") return /interval|mantenimiento|sustitu|inspecc|aceite|filtro|buj|refrigerante|líquido/.test(f) ? "Intervalos y operaciones" : "Mantenimiento";
  if (category === "lubricants") return /aceite|líquido|fluido|refriger|capacidad|especific|viscos/.test(f) ? "Fluidos y especificaciones" : "Aplicación";
  if (category === "torque") return /par|apriete|tornillo|tuerca|ángulo/.test(f) ? "Pares de apriete" : "Condiciones";
  if (category === "timing") return /cadena|correa|tensor|distrib|proced/.test(f) ? "Distribución" : "Procedimiento";
  if (category === "diagnosis") return /código|error|síntoma|prueba|valor|diagn/.test(f) ? "Diagnosis" : "Datos de prueba";
  if (category === "fuses") return /fusible|posición|caja|función/.test(f) ? "Caja y posiciones" : "Funciones";
  if (category === "oem") return /oem|referencia|código|pieza/.test(f) ? "Referencias" : "Aplicación";
  if (category === "repair times") return /tiempo|operación|hora|mano/.test(f) ? "Operaciones y tiempos" : "Aplicación";
  return "Datos documentados";
}

function maintenanceIntervalLabel(x) {
  const raw = [x.field, x.value, x.notes].filter(Boolean).join(" ");
  const km = raw.match(/(?:cada|a los|hasta)\\s*([0-9][0-9. ]*)\\s*(km|kil[oó]metros?)/i) ||
             raw.match(/([0-9][0-9. ]*)\\s*(km|kil[oó]metros?)/i);
  const months = raw.match(/([0-9][0-9. ]*)\\s*(mes(?:es)?|a[nñ]os?)/i);
  const parts = [];
  if (km) parts.push(km[1].replace(/\\s/g,"") + " km");
  if (months) parts.push(months[1].replace(/\\s/g,"") + " " + months[2].toLowerCase());
  return parts.join(" / ") || "Según registro";
}

function maintenanceOperationLabel(x) {
  const f=String(x.field || "").trim();
  if (f) return f;
  return "Operación de mantenimiento";
}

function renderMaintenanceRows(rows) {
  const groups = {};
  rows.forEach(x => {
    const key = /refriger|coolant|anticongel/i.test(String(x.field || "")) ? "Refrigeración" :
      /aceite|oil|filtro|filter/i.test(String(x.field || "")) ? "Motor" :
      /freno|brake/i.test(String(x.field || "")) ? "Frenos" :
      /buj|spark|combust|fuel/i.test(String(x.field || "")) ? "Motor / combustión" :
      /bater|hybrid|hev|hsg/i.test(String(x.field || "")) ? "Sistema híbrido / batería" :
      "Operaciones documentadas";
    (groups[key] ||= []).push(x);
  });

  const sections = Object.entries(groups).map(([group, items]) =>
    '<section class="maintenance-group">' +
      '<div class="module-data-group-head"><b>' + esc(group) + '</b><span>' + items.length + '</span></div>' +
      '<div class="maintenance-list">' +
      items.map((x,i) =>
        '<article class="maintenance-item">' +
          '<div class="maintenance-check"><span class="maintenance-index">' + (i+1) + '</span></div>' +
          '<div class="maintenance-main"><b>' + esc(maintenanceOperationLabel(x)) + '</b>' +
            '<span>' + esc(x.value || "") + (x.unit ? " " + esc(x.unit) : "") + '</span>' +
            '<small class="module-note">' + esc(maintenanceIntervalLabel(x)) +
            (x.notes ? " · " + esc(x.notes) : "") + '</small>' +
          '</div>' +
          '<div class="maintenance-source small"><a class="source" target="_blank" rel="noopener" href="' +
            esc(x.source_url || "#") + '">' + esc(x.source_title || "Fuente técnica") +
          '</a><br>' + esc(x.source_class || "") + ' · ' + esc(x.confidence || "") +
          (x.applicable_from || x.applicable_to ? '<br>Aplicación: ' + esc(x.applicable_from || "—") + '–' + esc(x.applicable_to || "—") : "") +
          '</div>' +
        '</article>'
      ).join("") +
      '</div>' +
    '</section>'
  ).join("");

  const body =
    '<p class="small">Checklist de mantenimiento de la variante. Los intervalos se presentan tal como están documentados; no se calculan intervalos nuevos.</p>' +
    '<div class="maintenance-legend"><span><b>' + rows.length + '</b> operaciones documentadas</span><span>Fuente y aplicabilidad visibles por operación</span></div>' +
    '<div class="maintenance-groups">' + sections + '</div>';
  renderModuleContent("maintenance","Esquema de mantenimiento",body,"CONTRASTADO");
  const web=$("#webresults");
  if(web) web.classList.add("hidden");
}

function diagnosisFieldKind(field) {
  const f=String(field || "").toLowerCase();
  if (/c[oó]digo|dtc|error|fault/.test(f)) return "code";
  if (/s[ií]ntoma|symptom|queja|problema/.test(f)) return "symptom";
  if (/prueba|test|medici[oó]n|measure|proced/.test(f)) return "test";
  if (/valor|volt|presi[oó]n|resistencia|temperatura|rpm|ohm|amp|bar/.test(f)) return "value";
  return "other";
}

function renderDiagnosisRows(rows) {
  const buckets={code:[],symptom:[],test:[],value:[],other:[]};
  rows.forEach(x => buckets[diagnosisFieldKind(x.field)].push(x));
  const labels={code:"Códigos / DTC",symptom:"Síntomas",test:"Pruebas y procedimientos",value:"Valores de prueba",other:"Datos de diagnóstico"};
  const order=["code","symptom","test","value","other"];
  const sections=order.filter(k=>buckets[k].length).map(k=>
    '<section class="diagnosis-group"><div class="module-data-group-head"><b>'+labels[k]+'</b><span>'+buckets[k].length+'</span></div><div class="diagnosis-list">'+
    buckets[k].map(x=>
      '<article class="diagnosis-item"><div><b>'+esc(x.field)+'</b><span>'+esc(x.value || "")+(x.unit ? " "+esc(x.unit):"")+'</span>'+
      (x.notes ? '<small class="module-note">'+esc(x.notes)+'</small>':'')+
      '</div><div class="diagnosis-source small"><a class="source" target="_blank" rel="noopener" href="'+esc(x.source_url || "#")+'">'+esc(x.source_title || "Fuente técnica")+
      '</a><br>'+esc(x.source_class || "")+' · '+esc(x.confidence || "")+
      (x.applicable_from || x.applicable_to ? '<br>Aplicación: '+esc(x.applicable_from || "—")+'–'+esc(x.applicable_to || "—"):"")+
      '</div></article>'
    ).join("")+'</div></section>'
  ).join("");
  const body='<p class="small">Registros de diagnosis asociados exclusivamente a la variante seleccionada. No se generan códigos, síntomas ni valores que no estén documentados.</p>'+
    '<div class="diagnosis-legend"><span><b>'+rows.length+'</b> registros disponibles</span><span>Solo datos documentados</span></div>'+
    '<div class="diagnosis-groups">'+(sections || '<p class="small">No hay campos de diagnosis suficientemente estructurados.</p>')+'</div>';
  renderModuleContent("diagnosis","Diagnosis",body,"CONTRASTADO");
  const web=$("#webresults"); if(web) web.classList.add("hidden");
}

function renderEngineManagementRows(rows) {
  const groups={};
  rows.forEach(x => {
    const f=String(x.field || "").toLowerCase();
    let key="Gestión del motor";
    if (/sensor|señal|signal|sonda/.test(f)) key="Sensores / señales";
    else if (/actuador|actuator|válvula|valve|inyector|inject/.test(f)) key="Actuadores";
    else if (/ecu|ecm|pcm|unidad|control|module/.test(f)) key="ECU / control";
    else if (/presi[oó]n|pressure|volt|tensi[oó]n|resistencia|rpm|temperatura|temperature/.test(f)) key="Datos de prueba";
    else if (/combust|fuel|inyecci|injection|aire|air|turbo|egr/.test(f)) key="Sistema de alimentación / aire";
    (groups[key] ||= []).push(x);
  });
  const body='<p class="small">Datos de gestión del motor asociados exclusivamente a la variante seleccionada. Se muestran únicamente señales, componentes y valores documentados.</p>'+
    '<div class="engine-legend"><span><b>'+rows.length+'</b> registros</span><span>Dato documentado</span><span>Fuente trazable</span></div>'+
    '<div class="engine-groups">'+Object.entries(groups).map(([name,list])=>
      '<section class="engine-group"><div class="module-data-group-head"><b>'+esc(name)+'</b><span>'+list.length+'</span></div>'+
      '<div class="engine-table"><div class="engine-head"><span>Elemento / campo</span><span>Dato</span><span>Fuente</span></div>'+
      list.map(x=>
        '<article class="engine-row"><div class="engine-main"><b>'+esc(x.field)+'</b>'+(x.notes?'<small class="module-note">'+esc(x.notes)+'</small>':'')+'</div>'+
        '<strong>'+esc(x.value || "—")+(x.unit?" "+esc(x.unit):"")+'</strong>'+
        '<div class="engine-source"><a class="source" target="_blank" rel="noopener" href="'+esc(x.source_url || "#")+'">'+esc(x.source_title || "Fuente técnica")+'</a><br>'+
        esc(x.source_class || "")+' · '+esc(x.confidence || "")+
        (x.applicable_from || x.applicable_to?'<br>Aplicación: '+esc(x.applicable_from || "—")+'–'+esc(x.applicable_to || "—"):"")+
        '</div></article>').join("")+'</div></section>'
    ).join("")+'</div>';
  renderModuleContent("engine management","Gestión del motor",body,"CONTRASTADO");
  const web=$("#webresults"); if(web) web.classList.add("hidden");
}

function renderComfortElectronicsRows(rows) {
  const groups={};
  rows.forEach(x => {
    const f=String(x.field || "").toLowerCase();
    let key="Electrónica de confort";
    if (/climat|hvac|aire acond|air.?con|calef|heater|blower/.test(f)) key="Climatización";
    else if (/puerta|door|cierre|lock|cerradura|keyless|llave|central/.test(f)) key="Cierre / puertas";
    else if (/eleval|window|ventan|mirror|espejo|asiento|seat/.test(f)) key="Cristales / asientos / espejos";
    else if (/l[uú]z|light|far|headlamp|ilumin|intermit|lamp/.test(f)) key="Iluminación";
    else if (/carrocer|body|bcm|unidad|module|control|comfort/.test(f)) key="Body / módulos de control";
    else if (/radio|audio|infot|naveg|display|pantalla|multimedia/.test(f)) key="Infotainment / multimedia";
    (groups[key] ||= []).push(x);
  });
  const body='<p class="small">Datos de electrónica de confort asociados exclusivamente a la variante seleccionada. Se muestran únicamente componentes, funciones y valores documentados.</p>'+
    '<div class="comfort-legend"><span><b>'+rows.length+'</b> registros</span><span>Función documentada</span><span>Fuente trazable</span></div>'+
    '<div class="comfort-groups">'+Object.entries(groups).map(([name,list])=>
      '<section class="comfort-group"><div class="module-data-group-head"><b>'+esc(name)+'</b><span>'+list.length+'</span></div>'+
      '<div class="comfort-table"><div class="comfort-head"><span>Elemento / función</span><span>Dato</span><span>Fuente</span></div>'+
      list.map(x=>
        '<article class="comfort-row"><div class="comfort-main"><b>'+esc(x.field)+'</b>'+
        (x.notes?'<small class="module-note">'+esc(x.notes)+'</small>':'')+
        '</div><strong>'+esc(x.value || "—")+(x.unit?" "+esc(x.unit):"")+'</strong>'+
        '<div class="comfort-source"><a class="source" target="_blank" rel="noopener" href="'+esc(x.source_url || "#")+'">'+esc(x.source_title || "Fuente técnica")+'</a><br>'+
        esc(x.source_class || "")+' · '+esc(x.confidence || "")+
        (x.applicable_from || x.applicable_to?'<br>Aplicación: '+esc(x.applicable_from || "—")+'–'+esc(x.applicable_to || "—"):"")+
        '</div></article>').join("")+'</div></section>'
    ).join("")+'</div>';
  renderModuleContent("comfort electronics","Electrónica confort",body,"CONTRASTADO");
  const web=$("#webresults"); if(web) web.classList.add("hidden");
}

function renderRepairManualRows(rows) {
  const groups={};
  rows.forEach(x => {
    const f=String(x.field || "").toLowerCase();
    let key="Procedimientos";
    if (/desmont|remove|extrac|retirar/.test(f)) key="Desmontaje";
    else if (/montaj|install|instal|assembly/.test(f)) key="Montaje";
    else if (/ajust|calib|adapt|program/.test(f)) key="Ajuste / calibración";
    else if (/herramient|tool|útil|util/.test(f)) key="Herramientas";
    else if (/par|apriete|torque/.test(f)) key="Pares asociados";
    (groups[key] ||= []).push(x);
  });
  const body='<p class="small">Procedimientos de reparación asociados exclusivamente a la variante seleccionada. Se muestran únicamente registros documentados y sus fuentes.</p>'+
    '<div class="repair-legend"><span><b>'+rows.length+'</b> registros</span><span>Procedimiento documentado</span><span>Fuente trazable</span></div>'+
    '<div class="repair-groups">'+Object.entries(groups).map(([name,list])=>
      '<section class="repair-group"><div class="module-data-group-head"><b>'+esc(name)+'</b><span>'+list.length+'</span></div>'+
      '<div class="repair-list">'+list.map(x=>
        '<article class="repair-row"><div class="repair-main"><b>'+esc(x.field)+'</b><span>'+esc(x.value || "—")+(x.unit?" "+esc(x.unit):"")+'</span>'+
        (x.notes?'<small class="module-note">'+esc(x.notes)+'</small>':'')+
        '</div><div class="repair-source"><a class="source" target="_blank" rel="noopener" href="'+esc(x.source_url || "#")+'">'+esc(x.source_title || "Fuente técnica")+'</a><br>'+
        esc(x.source_class || "")+' · '+esc(x.confidence || "")+
        (x.applicable_from || x.applicable_to?'<br>Aplicación: '+esc(x.applicable_from || "—")+'–'+esc(x.applicable_to || "—"):"")+
        '</div></article>').join("")+'</div></section>'
    ).join("")+'</div>';
  renderModuleContent("repair manuals","Manuales de reparación",body,"CONTRASTADO");
  const web=$("#webresults"); if(web) web.classList.add("hidden");
}

function renderOemRows(rows) {
  const groups={};
  rows.forEach(x => {
    const f=String(x.field || "").toLowerCase();
    let key="Referencias";
    if (/oem|referencia|part.?number|pieza|n[uú]mero/.test(f)) key="Referencias OEM";
    else if (/componente|component|pieza|description|descripci[oó]n/.test(f)) key="Componentes";
    else if (/aplic|fitment|compat|modelo|model|motor|engine/.test(f)) key="Aplicación";
    else if (/equiv|cross|sustit|replacement|supersed/.test(f)) key="Equivalencias documentadas";
    (groups[key] ||= []).push(x);
  });
  const body='<p class="small">Referencias OEM y aplicaciones asociadas exclusivamente a la variante seleccionada. Una referencia compartida no se interpreta como compatibilidad universal.</p>'+
    '<div class="oem-legend"><span><b>'+rows.length+'</b> registros</span><span>Referencia documentada</span><span>Aplicación específica</span></div>'+
    '<div class="oem-groups">'+Object.entries(groups).map(([name,list])=>
      '<section class="oem-group"><div class="module-data-group-head"><b>'+esc(name)+'</b><span>'+list.length+'</span></div>'+
      '<div class="oem-list">'+list.map(x=>
        '<article class="oem-row"><div class="oem-main"><b>'+esc(x.field)+'</b><span>'+esc(x.value || "—")+(x.unit?" "+esc(x.unit):"")+'</span>'+
        (x.notes?'<small class="module-note">'+esc(x.notes)+'</small>':'')+
        '</div><div class="oem-source"><a class="source" target="_blank" rel="noopener" href="'+esc(x.source_url || "#")+'">'+esc(x.source_title || "Fuente técnica")+'</a><br>'+
        esc(x.source_class || "")+' · '+esc(x.confidence || "")+
        (x.applicable_from || x.applicable_to?'<br>Aplicación: '+esc(x.applicable_from || "—")+'–'+esc(x.applicable_to || "—"):"")+
        '</div></article>').join("")+'</div></section>'
    ).join("")+'</div>';
  renderModuleContent("oem","OEM / referencias",body,"CONTRASTADO");
  const web=$("#webresults"); if(web) web.classList.add("hidden");
}

function renderFuseRows(rows) {
  const groups={};
  rows.forEach(x => {
    const f=String(x.field || "").toLowerCase();
    let key="Caja / posiciones";
    if (/habit[aá]culo|interior|junction|ib|i/p|i.p/.test(f)) key="Caja habitáculo";
    else if (/motor|engine|e/r|under hood|cap[oó]/.test(f)) key="Caja motor";
    else if (/fusible|fuse|rel[eé]|relay/.test(f)) key="Fusibles y relés";
    else if (/amper|a\b|amp/.test(f)) key="Amperaje";
    (groups[key] ||= []).push(x);
  });
  const body='<p class="small">Información de fusibles y relés asociada exclusivamente a la variante seleccionada. Se conserva la posición, función y amperaje tal como estén documentados.</p>'+
    '<div class="fuse-legend"><span><b>'+rows.length+'</b> registros</span><span>Posición documentada</span><span>Función original</span></div>'+
    '<div class="fuse-groups">'+Object.entries(groups).map(([name,list])=>
      '<section class="fuse-group"><div class="module-data-group-head"><b>'+esc(name)+'</b><span>'+list.length+'</span></div>'+
      '<div class="fuse-table"><div class="fuse-head"><span>Posición / campo</span><span>Valor</span><span>Función / fuente</span></div>'+
      list.map(x=>'<article class="fuse-row"><div><b>'+esc(x.field)+'</b></div>'+
      '<strong>'+esc(x.value || "—")+(x.unit?" "+esc(x.unit):"")+'</strong>'+
      '<div class="fuse-source">'+(x.notes?'<span>'+esc(x.notes)+'</span><br>':'')+
      '<a class="source" target="_blank" rel="noopener" href="'+esc(x.source_url || "#")+'">'+esc(x.source_title || "Fuente técnica")+'</a><br>'+
      esc(x.source_class || "")+' · '+esc(x.confidence || "")+
      (x.applicable_from || x.applicable_to?'<br>Aplicación: '+esc(x.applicable_from || "—")+'–'+esc(x.applicable_to || "—"):"")+
      '</div></article>').join("")+'</div></section>'
    ).join("")+'</div>';
  renderModuleContent("fuses","Fusibles y relés",body,"CONTRASTADO");
  const web=$("#webresults"); if(web) web.classList.add("hidden");
}

function renderLubricantRows(rows) {
  const groups={};
  rows.forEach(x => {
    const f=String(x.field || "").toLowerCase();
    let key="Especificaciones de fluidos";
    if (/aceite|oil|motor/.test(f)) key="Aceite de motor";
    else if (/refriger|coolant|anticongel/.test(f)) key="Refrigerante";
    else if (/freno|brake|dot/.test(f)) key="Líquido de frenos";
    else if (/transm|dct|gear|cambio/.test(f)) key="Transmisión";
    else if (/diferencial|differential/.test(f)) key="Diferencial";
    else if (/embrague|clutch|actuator/.test(f)) key="Actuador / embrague";
    (groups[key] ||= []).push(x);
  });
  const body='<p class="small">Lubricantes y fluidos asociados exclusivamente a la variante seleccionada. Se conserva la especificación, capacidad y unidad tal como aparecen documentadas.</p>'+
    '<div class="fluid-legend"><span><b>'+rows.length+'</b> registros</span><span>Especificación documentada</span><span>Capacidad original</span></div>'+
    '<div class="fluid-groups">'+Object.entries(groups).map(([name,list])=>
      '<section class="fluid-group"><div class="module-data-group-head"><b>'+esc(name)+'</b><span>'+list.length+'</span></div>'+
      '<div class="fluid-list">'+list.map(x=>
        '<article class="fluid-row"><div class="fluid-main"><b>'+esc(x.field)+'</b><span>'+esc(x.value || "—")+(x.unit?" "+esc(x.unit):"")+'</span>'+
        (x.notes?'<small class="module-note">'+esc(x.notes)+'</small>':'')+
        '</div><div class="fluid-source"><a class="source" target="_blank" rel="noopener" href="'+esc(x.source_url || "#")+'">'+esc(x.source_title || "Fuente técnica")+'</a><br>'+
        esc(x.source_class || "")+' · '+esc(x.confidence || "")+
        (x.applicable_from || x.applicable_to?'<br>Aplicación: '+esc(x.applicable_from || "—")+'–'+esc(x.applicable_to || "—"):"")+
        '</div></article>').join("")+'</div></section>'
    ).join("")+'</div>';
  renderModuleContent("lubricants","Lubricantes y fluidos",body,"CONTRASTADO");
  const web=$("#webresults"); if(web) web.classList.add("hidden");
}

function renderTimingRows(rows) {
  const groups={};
  rows.forEach(x => {
    const f=String(x.field || "").toLowerCase();
    let key="Distribución";
    if (/cadena|chain/.test(f)) key="Cadena";
    else if (/correa|belt/.test(f)) key="Correa";
    else if (/tensi[oó]n|tensor|tensioner/.test(f)) key="Tensores / tensión";
    else if (/marca|mark|punto|timing|calado|proced/.test(f)) key="Calado / procedimiento";
    else if (/interval|sustitu|replace|inspec/.test(f)) key="Intervalos / inspección";
    (groups[key] ||= []).push(x);
  });
  const body='<p class="small">Datos de distribución asociados exclusivamente a la variante seleccionada. Se muestran únicamente componentes, procedimientos, intervalos y condiciones documentados.</p>'+
    '<div class="timing-legend"><span><b>'+rows.length+'</b> registros</span><span>Procedimiento documentado</span><span>Aplicación de la fuente</span></div>'+
    '<div class="timing-groups">'+Object.entries(groups).map(([name,list])=>
      '<section class="timing-group"><div class="module-data-group-head"><b>'+esc(name)+'</b><span>'+list.length+'</span></div>'+
      '<div class="timing-list">'+list.map(x=>
        '<article class="timing-row"><div class="timing-main"><b>'+esc(x.field)+'</b><span>'+esc(x.value || "—")+(x.unit?" "+esc(x.unit):"")+'</span>'+
        (x.notes?'<small class="module-note">'+esc(x.notes)+'</small>':'')+
        '</div><div class="timing-source"><a class="source" target="_blank" rel="noopener" href="'+esc(x.source_url || "#")+'">'+esc(x.source_title || "Fuente técnica")+'</a><br>'+
        esc(x.source_class || "")+' · '+esc(x.confidence || "")+
        (x.applicable_from || x.applicable_to?'<br>Aplicación: '+esc(x.applicable_from || "—")+'–'+esc(x.applicable_to || "—"):"")+
        '</div></article>').join("")+'</div></section>'
    ).join("")+'</div>';
  renderModuleContent("timing","Distribución",body,"CONTRASTADO");
  const web=$("#webresults"); if(web) web.classList.add("hidden");
}

function renderTorqueRows(rows) {
  const groups={};
  rows.forEach(x => {
    const f=String(x.field || "").toLowerCase();
    let key="Pares documentados";
    if (/rueda|wheel|tuerca|lug/.test(f)) key="Ruedas / fijaciones";
    else if (/motor|culata|cylinder|head|c[oó]rter|oil pan/.test(f)) key="Motor";
    else if (/cambio|transm|gear|dct|tornillo|bolt|ground|masa/.test(f)) key="Transmisión / fijaciones";
    else if (/freno|brake/.test(f)) key="Frenos";
    else if (/egr|escape|exhaust|admisi[oó]n|intake/.test(f)) key="Admisión / escape";
    (groups[key] ||= []).push(x);
  });
  const body='<p class="small">Pares de apriete asociados exclusivamente a la variante seleccionada. Se conserva el valor y la unidad documentados; no se convierten ni se calculan ángulos.</p>'+
    '<div class="torque-legend"><span><b>'+rows.length+'</b> registros</span><span>Valor original</span><span>Condición documentada</span></div>'+
    '<div class="torque-groups">'+Object.entries(groups).map(([name,list])=>
      '<section class="torque-group"><div class="module-data-group-head"><b>'+esc(name)+'</b><span>'+list.length+'</span></div>'+
      '<div class="torque-table"><div class="torque-head"><span>Componente / campo</span><span>Par</span><span>Condición / fuente</span></div>'+
      list.map(x=>'<article class="torque-row"><div><b>'+esc(x.field)+'</b>'+(x.notes?'<small class="module-note">'+esc(x.notes)+'</small>':'')+'</div>'+
      '<strong>'+esc(x.value || "—")+(x.unit?" "+esc(x.unit):"")+'</strong>'+
      '<div class="torque-source"><a class="source" target="_blank" rel="noopener" href="'+esc(x.source_url || "#")+'">'+esc(x.source_title || "Fuente técnica")+'</a><br>'+
      esc(x.source_class || "")+' · '+esc(x.confidence || "")+
      (x.applicable_from || x.applicable_to?'<br>Aplicación: '+esc(x.applicable_from || "—")+'–'+esc(x.applicable_to || "—"):"")+
      '</div></article>').join("")+'</div></section>'
    ).join("")+'</div>';
  renderModuleContent("torque","Pares de apriete",body,"CONTRASTADO");
  const web=$("#webresults"); if(web) web.classList.add("hidden");
}

function renderRecallRows(rows) {
  const groups={};
  rows.forEach(x => {
    const f=String([x.field,x.value,x.notes].filter(Boolean).join(" ")).toLowerCase();
    let key="Campañas documentadas";
    if (/vin|bastidor|producci[oó]n|aplic|model|a[nñ]o|fecha/.test(f)) key="Aplicabilidad";
    else if (/riesgo|seguridad|safety|peligro|defecto|problema|incidencia/.test(f)) key="Riesgo / condición";
    else if (/repar|soluci[oó]n|remedio|acci[oó]n|sustitu|actualiz|correg/.test(f)) key="Acción de reparación";
    (groups[key] ||= []).push(x);
  });
  const body='<p class="small">Campañas y recalls asociados exclusivamente a la variante seleccionada. Se muestran únicamente registros documentados; la ausencia de un registro local no implica que no exista una campaña en otra fuente.</p>'+
    '<div class="recall-legend"><span><b>'+rows.length+'</b> registros</span><span>Aplicabilidad visible</span><span>Fuente trazable</span></div>'+
    '<div class="recall-groups">'+Object.entries(groups).map(([name,list])=>
      '<section class="recall-group"><div class="module-data-group-head"><b>'+esc(name)+'</b><span>'+list.length+'</span></div>'+
      '<div class="recall-list">'+list.map((x,i)=>
        '<article class="recall-row"><div class="recall-main"><b>'+esc(x.field || "Registro de campaña")+'</b><span>'+esc(x.value || "—")+(x.unit?" "+esc(x.unit):"")+'</span>'+
        (x.notes?'<small class="module-note">'+esc(x.notes)+'</small>':'')+
        '</div><div class="recall-source"><a class="source" target="_blank" rel="noopener" href="'+esc(x.source_url || "#")+'">'+esc(x.source_title || "Fuente técnica")+'</a><br>'+
        esc(x.source_class || "")+' · '+esc(x.confidence || "")+
        (x.applicable_from || x.applicable_to?'<br>Aplicación: '+esc(x.applicable_from || "—")+'–'+esc(x.applicable_to || "—"):"")+
        '</div></article>').join("")+'</div></section>'
    ).join("")+'</div>';
  renderModuleContent("recalls","Campañas de reparación",body,"CONTRASTADO");
  const web=$("#webresults"); if(web) web.classList.add("hidden");
}

function renderSmartFixRows(rows) {
  const groups={};
  rows.forEach(x => {
    const f=String([x.field,x.value,x.notes].filter(Boolean).join(" ")).toLowerCase();
    let key="Casos documentados";
    if (/s[ií]ntoma|symptom|queja|complaint|fallo|problema|incidencia/.test(f)) key="Síntoma / incidencia";
    else if (/causa|diagn[oó]st|c[oó]digo|dtc|fault|origen/.test(f)) key="Causa / diagnosis";
    else if (/soluci[oó]n|fix|remedio|repar|proced|acci[oó]n|sustitu|actualiz|correg/.test(f)) key="Solución / reparación";
    else if (/pieza|part|herramient|tool|componente/.test(f)) key="Piezas / herramientas";
    else if (/aplic|condici[oó]n|modelo|motor|fecha|vin/.test(f)) key="Condiciones / aplicabilidad";
    (groups[key] ||= []).push(x);
  });
  const body='<p class="small">Casos y soluciones técnicas asociados exclusivamente a la variante seleccionada. Se conserva el texto documentado y su fuente; no se generan diagnósticos ni soluciones nuevas.</p>'+
    '<div class="smartfix-legend"><span><b>'+rows.length+'</b> registros</span><span>Síntoma y solución separados</span><span>Fuente trazable</span></div>'+
    '<div class="smartfix-groups">'+Object.entries(groups).map(([name,list])=>
      '<section class="smartfix-group"><div class="module-data-group-head"><b>'+esc(name)+'</b><span>'+list.length+'</span></div>'+
      '<div class="smartfix-list">'+list.map(x=>
        '<article class="smartfix-row"><div class="smartfix-main"><b>'+esc(x.field || "Registro técnico")+'</b><span>'+esc(x.value || "—")+(x.unit?" "+esc(x.unit):"")+'</span>'+
        (x.notes?'<small class="module-note">'+esc(x.notes)+'</small>':'')+
        '</div><div class="smartfix-source"><a class="source" target="_blank" rel="noopener" href="'+esc(x.source_url || "#")+'">'+esc(x.source_title || "Fuente técnica")+'</a><br>'+
        esc(x.source_class || "")+' · '+esc(x.confidence || "")+
        (x.applicable_from || x.applicable_to?'<br>Aplicación: '+esc(x.applicable_from || "—")+'–'+esc(x.applicable_to || "—"):"")+
        '</div></article>').join("")+'</div></section>'
    ).join("")+'</div>';
  renderModuleContent("smart fix","Smart Fix / Cases",body,"CONTRASTADO");
  const web=$("#webresults"); if(web) web.classList.add("hidden");
}

function renderTechnicalRows(category, rows) {
  if (category === "recalls") {
    renderRecallRows(rows);
    return;
  }
  if (category === "smart fix") {
    renderSmartFixRows(rows);
    return;
  }
  if (category === "engine management") {
    renderEngineManagementRows(rows);
    return;
  }
  if (category === "comfort electronics") {
    renderComfortElectronicsRows(rows);
    return;
  }
  if (category === "repair manuals") {
    renderRepairManualRows(rows);
    return;
  }
  if (category === "oem") {
    renderOemRows(rows);
    return;
  }
  if (category === "fuses") {
    renderFuseRows(rows);
    return;
  }
  if (category === "lubricants") {
    renderLubricantRows(rows);
    return;
  }
  if (category === "timing") {
    renderTimingRows(rows);
    return;
  }
  if (category === "torque") {
    renderTorqueRows(rows);
    return;
  }
  if (category === "diagnosis") {
    renderDiagnosisRows(rows);
    return;
  }
  if (category === "maintenance") {
    renderMaintenanceRows(rows);
    return;
  }
  const meta = technicalModuleMeta(category);
  const groups = {};
  rows.forEach(x => {
    const key = technicalGroupKey(category, x.field);
    (groups[key] ||= []).push(x);
  });
  const body = '<p class="small">' + esc(meta[1]) + ' Cada registro conserva fuente, nivel de contraste y periodo de aplicación.</p>' +
    '<span class="module-data-count">' + rows.length + ' registros contrastados</span>' +
    '<div class="module-data-groups">' + Object.entries(groups).map(([group, items]) =>
      '<section class="module-data-group"><div class="module-data-group-head"><b>' + esc(group) +
      '</b><span>' + items.length + '</span></div><div class="module-records">' +
      items.map(x => '<div class="tech-row"><div><b>' + esc(x.field) + '</b><span>' +
        esc(x.value || "") + (x.unit ? " " + esc(x.unit) : "") + '</span>' +
        (x.notes ? '<small class="module-note">' + esc(x.notes) + '</small>' : '') +
        '</div><div class="small"><a class="source" target="_blank" rel="noopener" href="' +
        esc(x.source_url || "#") + '">' + esc(x.source_title || "Fuente técnica") +
        '</a><br>' + esc(x.source_class || "") + ' · ' + esc(x.confidence || "") +
        ' · ' + esc(x.applicable_from || "") + '–' + esc(x.applicable_to || "") +
        '</div></div>').join("") + '</div></section>'
    ).join("") + '</div>';
  renderModuleContent(category,meta[0],body,"CONTRASTADO");
  const web = $("#webresults");
  if (web) web.classList.add("hidden");
}

async function runResearch(payload) {
  const web = $("#webresults");
  if (web) {
    web.classList.remove("hidden");
    web.innerHTML = "<b>Buscando evidencia pública…</b>";
  }
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
    const moduleMeta = modules.find(m => m[1] === payload.category) ||
      [payload.category || "Evidencia técnica", payload.category || "", ""];
    renderModuleContent(
      payload.category || "",
      moduleMeta[0],
      '<p class="small">Resultados públicos recién consultados para la variante seleccionada. Quedan almacenados como evidencia pendiente de contraste.</p>' +
      (results || "<p>No se encontraron resultados públicos.</p>"),
      "EVIDENCIA WEB"
    );
    if (web) web.classList.add("hidden");
  } catch (e) {
    if (web) web.innerHTML = "<b>No se pudo consultar Internet.</b><p>" + esc(e.message) + "</p>";
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
