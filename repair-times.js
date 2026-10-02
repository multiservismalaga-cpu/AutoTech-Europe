(() => {
  if (typeof window.renderTechnicalRows !== "function") return;
  const baseRenderTechnicalRows = window.renderTechnicalRows;

  function repairTimeGroup(field) {
    const f = String(field || "").toLowerCase();
    if (/motor|engine|culata|cylinder|c[oó]rter/.test(f)) return "Motor";
    if (/transm|cambio|gear|dct|embrague|clutch/.test(f)) return "Transmisión";
    if (/freno|brake|suspensi[oó]n|direcci[oó]n|steer/.test(f)) return "Chasis / frenos";
    if (/carrocer|body|puerta|door|paragolpes|bumper/.test(f)) return "Carrocería";
    if (/el[eé]ctric|electr|bater|battery|diagn/.test(f)) return "Electricidad / diagnosis";
    return "Operaciones documentadas";
  }

  function renderRepairTimeRows(rows) {
    const groups = {};
    rows.forEach(x => (groups[repairTimeGroup(x.field)] ||= []).push(x));
    const sections = Object.entries(groups).map(([name, list]) =>
      '<section class="repair-time-group"><div class="module-data-group-head"><b>' + esc(name) + '</b><span>' + list.length + '</span></div>' +
      '<div class="repair-time-table"><div class="repair-time-head"><span>Operación</span><span>Tiempo documentado</span><span>Condiciones / fuente</span></div>' +
      list.map(x => '<article class="repair-time-row"><div class="repair-time-operation"><b>' + esc(x.field) + '</b>' +
        (x.notes ? '<small class="module-note">' + esc(x.notes) + '</small>' : '') + '</div>' +
        '<strong>' + esc(x.value || "—") + (x.unit ? ' ' + esc(x.unit) : '') + '</strong>' +
        '<div class="repair-time-source"><a class="source" target="_blank" rel="noopener" href="' + esc(x.source_url || "#") + '">' + esc(x.source_title || "Fuente técnica") + '</a><br>' +
        esc(x.source_class || "") + ' · ' + esc(x.confidence || "") +
        (x.applicable_from || x.applicable_to ? '<br>Aplicación: ' + esc(x.applicable_from || "—") + '–' + esc(x.applicable_to || "—") : '') +
        '</div></article>').join("") + '</div></section>'
    ).join("");
    const body = '<p class="small">Tiempos de reparación asociados exclusivamente a la variante seleccionada. Se conserva el tiempo publicado y sus condiciones; no se estiman horas de mano de obra ausentes.</p>' +
      '<div class="repair-time-legend"><span><b>' + rows.length + '</b> operaciones</span><span>Tiempo original</span><span>Aplicación documentada</span></div>' +
      '<div class="repair-time-groups">' + sections + '</div>';
    renderModuleContent("repair times", "Tiempos de reparación", body, "CONTRASTADO");
    const web = $("#webresults"); if (web) web.classList.add("hidden");
  }

  window.renderTechnicalRows = function(category, rows) {
    if (category === "repair times") {
      renderRepairTimeRows(rows);
      return;
    }
    return baseRenderTechnicalRows(category, rows);
  };

  const style = document.createElement("style");
  style.textContent = `
    .repair-time-legend{display:flex;flex-wrap:wrap;gap:7px;margin:8px 0 12px}
    .repair-time-legend span{padding:6px 9px;border:1px solid #244d69;border-radius:999px;color:#9fc0d5;font-size:10px}
    .repair-time-groups{display:grid;gap:12px}
    .repair-time-group{padding:10px;border:1px solid #193f60;border-radius:11px;background:#071728}
    .repair-time-table{display:grid;gap:6px}
    .repair-time-head,.repair-time-row{display:grid;grid-template-columns:minmax(170px,1.2fr) minmax(110px,.55fr) minmax(180px,1fr);gap:10px;align-items:center}
    .repair-time-head{padding:5px 8px;color:#789db7;font-size:9px;text-transform:uppercase;letter-spacing:.05em}
    .repair-time-row{padding:9px;border:1px solid #17364f;border-radius:9px;background:#081a2a}
    .repair-time-operation{display:grid;gap:3px;min-width:0}
    .repair-time-operation>b{color:#d9edf8;font-size:11px}
    .repair-time-row>strong{font-size:13px;color:#f0f7fb;white-space:nowrap}
    .repair-time-source{font-size:10px;line-height:1.45;min-width:0}
    @media(max-width:760px){.repair-time-head{display:none}.repair-time-row{grid-template-columns:1fr}}
  `;
  document.head.appendChild(style);
})();
