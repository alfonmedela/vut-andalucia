// VUT Andalucía · interfaz. Sin dependencias ni paso de compilación.
// Todo texto que viene del usuario o del modelo se inserta con textContent, nunca como HTML.
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const el = (tag, props = {}, ...hijos) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) n.setAttribute(k, v === true ? "" : v);
  }
  for (const h of hijos.flat()) if (h !== null && h !== undefined) n.append(h);
  return n;
};

const ROTULOS = {
  regla: "Lo que dice la norma",
  excepcion: "Excepciones y matices",
  paso: "Qué hacer",
  riesgo: "Riesgos",
};
const CLASES = { norma: "Norma", jurisprudencia: "Sentencia", doctrina: "Criterio administrativo" };
const TERRITORIOS = { ue: "Unión Europea", estatal: "España", andalucia: "Andalucía", municipal: "Municipal" };
const ABSTENCION = {
  sin_fragmentos: "Ninguna norma del corpus vigente en esa fecha trata de lo que preguntas.",
  fuera_de_ambito: "Los textos encontrados no responden a tu pregunta. Puede que esté fuera del corpus (otra comunidad autónoma, un municipio aún no incluido, fiscalidad no turística…).",
  citas_no_verificadas: "El modelo propuso una respuesta, pero ninguna de sus citas aparece literalmente en el texto oficial, así que no se muestra.",
  no_respaldadas: "Las citas existen en el texto oficial, pero la revisión no ve que prueben lo que el modelo afirmaba, así que no se muestra nada.",
  instrucciones: "Tu mensaje contiene instrucciones para el asistente. Solo respondo preguntas sobre la normativa, siempre con citas.",
  otro_territorio: "Tu pregunta trata de otra comunidad autónoma o país. Este asistente solo contiene la normativa andaluza, la estatal y la europea.",
  municipio_sin_normas: "El corpus aún no contiene normas de ese municipio, así que no puedo decirte qué establecen.",
};

const fmtFecha = (iso) => iso ? new Date(iso + "T12:00:00").toLocaleDateString("es-ES", { day: "numeric", month: "short", year: "numeric" }) : "hoy";
const hoyISO = () => new Date().toISOString().slice(0, 10);
// Una redacción ya publicada que entra en vigor después de la fecha consultada.
const esFutura = (f) => f.vigente_desde > (actual?.fecha || $("#fecha").value || hoyISO());
const vigencia = (f) => esFutura(f)
  ? `Entra en vigor el ${fmtFecha(f.vigente_desde)}`
  : `${fmtFecha(f.vigente_desde)} → ${fmtFecha(f.vigente_hasta)}${f.vigencia_incierta ? " (fecha no publicada)" : ""}`;
const seguro = (url) => (typeof url === "string" && /^https:\/\//.test(url) ? url : null);

let municipios = [];
let actual = null; // última respuesta

// ── arranque ───────────────────────────────────────────
async function iniciar() {
  $("#fecha").value = hoyISO();
  window.addEventListener("hashchange", mostrarVista);
  mostrarVista();
  rotarEjemplos();
  $("#formulario").addEventListener("submit", (e) => { e.preventDefault(); enviar(); });
  $("#exportar").addEventListener("click", exportarInforme);
  $("#pregunta").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); enviar(); }
  });
  try {
    const [estado, lista] = await Promise.all([api("/api/estado"), api("/api/municipios")]);
    document.querySelectorAll('[data-cifra="fuentes"]').forEach((e) => { e.textContent = estado.fuentes.toLocaleString("es-ES"); });
    document.querySelectorAll('[data-cifra="fragmentos"]').forEach((e) => { e.textContent = estado.fragmentos.toLocaleString("es-ES"); });
    municipios = lista;
    const dl = $("#lista-municipios");
    for (const m of lista.sort((a, b) => (b.con_normas - a.con_normas) || a.nombre.localeCompare(b.nombre, "es"))) {
      dl.append(el("option", { value: m.nombre, label: `${m.provincia}${m.con_normas ? " · con normas propias" : ""}` }));
    }
  } catch (e) { console.warn("No se pudo cargar el estado", e); }
  cargarFuentes();
  const q = new URLSearchParams(location.search).get("q");
  // Un enlace con ?q= rellena la pregunta, pero no la envía: gastar una consulta exige un clic.
  if (q) { $("#pregunta").value = q.slice(0, 1200); $("#pregunta").focus(); }
}

// ── vistas: Preguntar, Cómo funciona y Fuentes son páginas distintas ──
const VISTAS = ["preguntar", "como", "fuentes"];
function mostrarVista() {
  const pedida = location.hash.slice(1);
  const vista = VISTAS.includes(pedida) ? pedida : "preguntar";
  document.querySelectorAll(".vista").forEach((v) => { v.hidden = v.dataset.vista !== vista; });
  document.querySelectorAll(".bar__nav a[data-vista]").forEach((a) => {
    if (a.dataset.vista === vista) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  if (VISTAS.includes(pedida)) window.scrollTo({ top: 0 });
}

// Ejemplos de pregunta: rotan como texto de muestra mientras la caja está vacía.
const EJEMPLOS = [
  "¿Necesito permiso de la comunidad para alquilar mi piso a turistas?",
  "¿Puedo dar de alta un piso turístico nuevo en el centro de Málaga?",
  "Si vendo mi piso turístico, ¿el comprador mantiene la inscripción?",
  "¿Tengo que cobrar IVA si alquilo por menos de 30 noches?",
  "¿Cuántos metros cuadrados por plaza exige la Junta?",
];
function rotarEjemplos() {
  const caja = $("#pregunta");
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  let i = 0;
  setInterval(() => {
    if (caja.value || document.activeElement === caja) return;
    caja.classList.add("cambiando");
    setTimeout(() => {
      i = (i + 1) % EJEMPLOS.length;
      caja.placeholder = EJEMPLOS[i];
      caja.classList.remove("cambiando");
    }, 300);
  }, 4200);
}

async function api(url, opciones) {
  const r = await fetch(url, opciones);
  if (!r.ok) {
    let detalle = `${r.status}`;
    try { detalle = (await r.json()).detail || detalle; } catch {}
    throw new Error(typeof detalle === "string" ? detalle : JSON.stringify(detalle));
  }
  return r.json();
}

// ── pregunta ───────────────────────────────────────────
async function enviar() {
  let pregunta = $("#pregunta").value.trim();
  // Caja vacía: se pregunta el ejemplo que se está mostrando.
  if (!pregunta) { pregunta = $("#pregunta").placeholder; $("#pregunta").value = pregunta; }
  if (pregunta.length < 3) { $("#pregunta").focus(); return; }
  const nombre = $("#municipio").value.trim();
  const m = municipios.find((x) => x.nombre.toLowerCase() === nombre.toLowerCase());
  const cuerpo = { pregunta, fecha: $("#fecha").value || null, municipio: m ? m.slug : (nombre || null) };

  const res = $("#resultado");
  res.hidden = false;
  $("#panel").hidden = true;
  $("#respuesta").replaceChildren();
  $("#avisos").replaceChildren();
  $("#pendientes").hidden = true;
  $("#consultadas").hidden = true;
  $("#pie-respuesta").textContent = "";
  $("#acciones").hidden = true;
  $("#informe").replaceChildren();
  document.body.classList.remove("con-informe");
  actual = null;
  pintarContexto(pregunta, cuerpo.fecha, m ? m.nombre : nombre, false);
  marcarPaso("buscar");
  $("#enviar").disabled = true;
  res.scrollIntoView({ behavior: "smooth", block: "start" });
  history.replaceState(null, "", `?q=${encodeURIComponent(pregunta)}`);

  try {
    const r = await fetch("/api/preguntar", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(cuerpo),
    });
    if (!r.ok) {
      let d = r.status; try { d = (await r.json()).detail; } catch {}
      throw new Error(typeof d === "string" ? d : JSON.stringify(d));
    }
    await leerEventos(r.body, (nombreEv, datos) => {
      if (nombreEv === "fuentes") { pintarConsultadas(datos); marcarPaso("redactar"); }
      else if (nombreEv === "respuesta") { marcarPaso("verificar"); pintarRespuesta(datos); }
      else if (nombreEv === "error") { pintarError(datos.mensaje); }
      else if (nombreEv === "solo_busqueda") { pintarSoloBusqueda(datos.mensaje); }
    });
  } catch (e) {
    pintarError(e.message || String(e));
  } finally {
    $("#enviar").disabled = false;
  }
}

async function leerEventos(cuerpo, alEvento) {
  const lector = cuerpo.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await lector.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const bloque = buf.slice(0, i); buf = buf.slice(i + 2);
      const ev = /^event: (.+)$/m.exec(bloque)?.[1];
      const data = /^data: (.*)$/m.exec(bloque)?.[1];
      if (ev && data) alEvento(ev, JSON.parse(data));
    }
  }
}

const TEXTO_PASO = {
  buscar: "Buscando en las fuentes",
  redactar: "Redactando y comprobando cada cita",
  verificar: "Verificando",
};
let cronometro = null;
function terminarProgreso() {
  $("#progreso").hidden = true;
  clearInterval(cronometro);
  cronometro = null;
}
function marcarPaso(paso) {
  const orden = ["buscar", "redactar", "verificar"];
  const lista = $("#pasos");
  const caja = $("#progreso");
  if (caja.hidden) {
    caja.hidden = false;
    const inicio = performance.now();
    $("#cronometro").textContent = "0";
    clearInterval(cronometro);
    cronometro = setInterval(() => { $("#cronometro").textContent = String(Math.round((performance.now() - inicio) / 1000)); }, 500);
  }
  $("#paso-actual").textContent = TEXTO_PASO[paso];
  lista.querySelectorAll("li").forEach((li) => {
    const i = orden.indexOf(li.dataset.paso), j = orden.indexOf(paso);
    li.classList.toggle("hecho", i < j);
    li.classList.toggle("activo", i === j);
  });
}

function pintarContexto(pregunta, fecha, municipio, deducido) {
  const c = $("#contexto");
  c.replaceChildren(
    el("p", { class: "q", text: pregunta }),
    el("span", { class: "etiqueta", text: `Ley vigente a ${fmtFecha(fecha)}` }),
    municipio ? el("span", { class: "etiqueta etiqueta--acento", text: `${municipio}${deducido ? " · deducido de la pregunta" : ""}` }) : el("span", { class: "etiqueta", text: "Toda Andalucía" }),
  );
}

function pintarConsultadas(datos) {
  const ol = $("#lista-consultadas");
  ol.replaceChildren(...datos.fragmentos.map((f) => el("li", {},
    el("button", { type: "button", text: `${f.cita} — ${f.encabezado.slice(0, 80)}`, onclick: () => abrirPanel(f, null) }))));
  $("#consultadas").hidden = datos.fragmentos.length === 0;
  $("#consultadas summary").textContent = `Fuentes consultadas (${datos.fragmentos.length})`;
  pintarAvisos(datos.avisos);
}

function pintarAvisos(avisos) {
  const c = $("#avisos");
  c.replaceChildren();
  if (!avisos || !avisos.length) return;
  c.append(el("h3", { text: "Ten en cuenta" }), ...avisos.map((a) => el("p", { class: "aviso", text: a })));
}

function pintarRespuesta(r) {
  actual = r;
  terminarProgreso();
  if (r.municipio) pintarContexto(r.pregunta, r.fecha, r.municipio.nombre, r.municipio_detectado);
  const cont = $("#respuesta");
  cont.replaceChildren();

  if (r.motivo_abstencion) {
    cont.append(el("div", { class: "tarjeta tarjeta--abstencion" },
      el("h3", { text: "No puedo afirmarlo con una cita." }),
      el("p", { text: ABSTENCION[r.motivo_abstencion] || "" })));
  }

  // Numeración de fuentes por orden de aparición
  const numeros = new Map();
  for (const a of r.afirmaciones) if (!numeros.has(a.fragmento.id)) numeros.set(a.fragmento.id, numeros.size + 1);

  const grupos = agrupar(r.afirmaciones);
  for (const { clave, titulo, lista: grupo } of grupos) {
    const sec = el("section", { class: `grupo grupo--${clave}` }, grupos.length > 1 ? el("h3", { text: titulo }) : null);
    for (const a of grupo) {
      const n = numeros.get(a.fragmento.id);
      const ref = el("button", {
        type: "button", class: "ref", "aria-pressed": "false",
        "aria-label": `Ver la cita ${n}: ${a.fragmento.cita}`,
        text: `${n} · ${a.fragmento.cita}`,
        onclick: (e) => { abrirPanel(a.fragmento, a.cita_literal); marcarRef(e.currentTarget); },
      });
      sec.append(el("p", { class: "afirmacion" }, a.texto, " ", ref));
    }
    cont.append(sec);
  }

  if (r.preguntas_pendientes.length) {
    const p = $("#pendientes");
    p.hidden = false;
    p.replaceChildren(el("h3", { text: "Para afinar la respuesta" }),
      ...r.preguntas_pendientes.map((q) => el("button", { type: "button", text: q, onclick: () => afinar(q) })));
  }
  pintarAvisos(r.avisos);
  const pie = ["Las frases las redacta el modelo; la prueba es la cita oficial, comprobada palabra por palabra", `Modelo: ${r.modelo}`, `búsqueda ${r.busqueda}`];
  if (r.descartadas) pie.push(`${r.descartadas} afirmación(es) descartada(s) por no poder verificar su cita`);
  const corregidas = r.afirmaciones.filter((a) => a.corregida).length;
  if (corregidas) pie.push(`${corregidas} afirmación(es) reformulada(s) en la revisión para ceñirlas a su cita`);
  $("#pie-respuesta").textContent = pie.join(" · ");

  // El informe se monta ya, para que imprimir la página (⌘P) también lo produzca.
  $("#informe").replaceChildren(...construirInforme(r));
  document.body.classList.add("con-informe");
  $("#acciones").hidden = false;

  const primera = cont.querySelector(".ref");
  if (primera && window.matchMedia("(min-width: 961px)").matches) primera.click();
}

function marcarRef(boton) {
  document.querySelectorAll(".ref[aria-pressed='true']").forEach((b) => b.setAttribute("aria-pressed", "false"));
  boton.setAttribute("aria-pressed", "true");
}

function afinar(pregunta) {
  const base = actual ? actual.pregunta : "";
  $("#pregunta").value = `${base}\nDato adicional: ${pregunta.replace(/^¿|\?$/g, "")}: `;
  $("#pregunta").focus();
  window.scrollTo({ top: $("#formulario").offsetTop - 90, behavior: "smooth" });
}

function pintarSoloBusqueda(mensaje) {
  terminarProgreso();
  $("#respuesta").replaceChildren(el("div", { class: "tarjeta" }, el("p", { text: mensaje })));
  const d = $("#consultadas");
  d.open = true;
  const primero = d.querySelector("button");
  if (primero && window.matchMedia("(min-width: 961px)").matches) primero.click();
}

function pintarError(mensaje) {
  terminarProgreso();
  $("#respuesta").replaceChildren(el("div", { class: "tarjeta tarjeta--error", role: "alert" },
    el("strong", { text: "No se pudo completar la respuesta. " }), mensaje));
}

// ── panel de cita ──────────────────────────────────────
function buscarCita(texto, cita) {
  // Posición de la cita en el texto; tolera mayúsculas, espacios, comillas y puntos suspensivos en los extremos.
  if (!cita) return null;
  const limpia = cita.replace(/^[\s«“"'(\[.…]+|[\s»”"')\].…]+$/g, "");
  const palabras = limpia.split(/\s+/).filter(Boolean).map((p) => p.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (!palabras.length) return null;
  const m = new RegExp(palabras.join("[\\s«»“”\"']+"), "i").exec(texto);
  return m ? [m.index, m.index + m[0].length] : null;
}

function resaltarVarias(texto, citas) {
  // Nodos del texto con cada cita marcada; los tramos solapados se funden.
  const tramos = citas.map((c) => buscarCita(texto, c)).filter(Boolean).sort((a, b) => a[0] - b[0]);
  const frag = document.createDocumentFragment();
  let pos = 0;
  for (const [ini, fin] of tramos) {
    if (fin <= pos) continue;
    const desde = Math.max(ini, pos);
    frag.append(texto.slice(pos, desde), el("mark", { text: texto.slice(desde, fin) }));
    pos = fin;
  }
  frag.append(texto.slice(pos));
  return [frag, tramos.length > 0];
}

const resaltar = (texto, cita) => resaltarVarias(texto, cita ? [cita] : []);

async function abrirPanel(f, cita) {
  const panel = $("#panel");
  panel.hidden = false;
  pintarPanel(f, cita, null);
  try {
    const d = await api(`/api/fragmento/${encodeURIComponent(f.id)}`);
    pintarPanel(f, cita, d.versiones);
  } catch (e) { console.warn(e); }
}

function pintarPanel(f, cita, versiones) {
  const n = f.norma || {};
  const [cuerpo, encontrada] = resaltar(f.texto, cita);
  const marca = el("div", { class: "texto" }, cuerpo);
  const hijos = [
    el("button", { class: "cerrar", type: "button", "aria-label": "Cerrar", text: "×", onclick: () => { $("#panel").hidden = true; } }),
    el("div", { class: "panel__insignias" },
      el("span", { class: "etiqueta etiqueta--acento", text: CLASES[n.clase] || "Norma" }),
      el("span", { class: "etiqueta", text: n.municipio ? `Municipal · ${n.municipio}` : (TERRITORIOS[n.territorio] || "") }),
      el("span", { class: `etiqueta${esFutura(f) ? " etiqueta--acento" : ""}`, text: vigencia(f) })),
    el("h3", { text: f.cita }),
    el("p", { class: "norma", text: n.titulo || "" }),
    f.encabezado ? el("p", { class: "encabezado", text: f.encabezado }) : null,
    marca,
    cita && encontrada ? el("p", { class: "verificada", text: "La cita aparece literalmente en este texto oficial." }) : null,
    ...(f.avisos || []).map((a) => el("p", { class: "aviso", text: a })),
    versiones && versiones.length > 1 ? lineaTemporal(versiones, f, cita) : null,
    seguro(f.url) ? el("a", { class: "enlace", href: seguro(f.url), target: "_blank", rel: "noopener", text: "Abrir en la fuente oficial ↗" }) : null,
  ];
  $("#panel-contenido").replaceChildren(...hijos.filter((h) => h !== null && h !== undefined));
}

function lineaTemporal(versiones, actualF, cita) {
  const hoy = hoyISO();
  const detalle = el("p", { class: "linea__detalle" });
  const barra = el("div", { class: "linea__barra", role: "group", "aria-label": "Versiones del precepto" });
  for (const v of versiones) {
    const vigenteHoy = v.vigente_desde <= hoy && (!v.vigente_hasta || hoy < v.vigente_hasta);
    const b = el("button", {
      type: "button", class: `linea__tramo${vigenteHoy ? " vigente-hoy" : ""}`,
      "aria-current": v.id === actualF.id ? "true" : "false",
      "aria-label": `Versión del ${fmtFecha(v.vigente_desde)} al ${fmtFecha(v.vigente_hasta)}`,
      onclick: () => pintarPanel(v, v.id === actualF.id ? cita : null, versiones),
      onmouseenter: () => { detalle.textContent = `${fmtFecha(v.vigente_desde)} → ${fmtFecha(v.vigente_hasta)}${vigenteHoy ? " · vigente hoy" : ""}`; },
    });
    barra.append(b);
  }
  detalle.textContent = `${versiones.length} versiones. Pulsa un tramo para leer el texto de entonces.`;
  return el("div", { class: "linea" },
    el("p", { class: "linea__titulo", text: "Historia de este precepto" }), barra,
    el("div", { class: "linea__fechas" }, el("span", { text: fmtFecha(versiones[0].vigente_desde) }), el("span", { text: "hoy" })),
    detalle);
}

// ── agrupación de la respuesta ─────────────────────────
// Lo vigente se agrupa por tipo; lo que se apoya en una redacción ya publicada que aún no rige
// va aparte, por fecha, justo después de la regla: es lo primero que necesita saber el usuario.
function agrupar(afirmaciones) {
  const actuales = afirmaciones.filter((a) => !esFutura(a.fragmento));
  const fechas = [...new Set(afirmaciones.filter((a) => esFutura(a.fragmento)).map((a) => a.fragmento.vigente_desde))].sort();
  const grupos = [];
  const porTipo = (tipo) => actuales.filter((a) => a.tipo === tipo);
  if (porTipo("regla").length) grupos.push({ clave: "regla", titulo: ROTULOS.regla, lista: porTipo("regla") });
  for (const f of fechas) {
    grupos.push({ clave: "cambio", titulo: `Lo que cambia el ${fmtLarga(f)}`, lista: afirmaciones.filter((a) => esFutura(a.fragmento) && a.fragmento.vigente_desde === f) });
  }
  for (const tipo of ["excepcion", "paso", "riesgo"]) {
    if (porTipo(tipo).length) grupos.push({ clave: tipo, titulo: ROTULOS[tipo], lista: porTipo(tipo) });
  }
  return grupos;
}

// ── informe para imprimir o guardar en PDF ─────────────
const fmtLarga = (iso) => new Date(iso + "T12:00:00").toLocaleDateString("es-ES", { day: "numeric", month: "long", year: "numeric" });

function referencia(r) {
  // Identificador corto y estable para la misma consulta en el mismo día.
  let h = 2166136261;
  for (const c of `${r.pregunta}|${r.fecha}|${r.municipio?.slug || ""}`) h = Math.imul(h ^ c.codePointAt(0), 16777619);
  return `VUT-${hoyISO().replaceAll("-", "")}-${(h >>> 0).toString(36).toUpperCase().padStart(7, "0").slice(-5)}`;
}

function construirInforme(r) {
  // Fuentes numeradas por orden de aparición, como en pantalla, con todas sus citas.
  const fuentes = new Map();
  for (const a of r.afirmaciones) {
    if (!fuentes.has(a.fragmento.id)) fuentes.set(a.fragmento.id, { n: fuentes.size + 1, f: a.fragmento, citas: [] });
    const e = fuentes.get(a.fragmento.id);
    if (!e.citas.includes(a.cita_literal)) e.citas.push(a.cita_literal);
  }
  const lista = [...fuentes.values()];
  const dato = (rotulo, valor) => el("div", {}, el("dt", { text: rotulo }), el("dd", { text: valor }));
  const afirmacion = (a) => {
    const e = fuentes.get(a.fragmento.id);
    return el("p", { class: "inf-af" }, a.texto, el("span", { class: "inf-ref", text: `[${e.n}] ${a.fragmento.cita}` }));
  };
  const seccion = (n, titulo, ...hijos) => el("section", { class: "inf-sec" },
    el("h2", {}, el("span", { class: "inf-sec__n", text: String(n).padStart(2, "0") }), titulo), ...hijos);

  const m = r.municipio;
  const lugar = !m ? "Toda Andalucía" : (m.provincia && m.provincia !== m.nombre ? `${m.nombre} (${m.provincia})` : m.nombre);
  const nCitas = r.afirmaciones.length;
  const partes = [
    el("header", { class: "inf-cab" },
      el("span", { class: "wm" }, el("span", { text: "Alfonso" }), " ", el("span", { class: "wm__m", text: "Medela" })),
      el("span", { text: "VUT Andalucía · Informe normativo" })),
    el("section", { class: "inf-portada" },
      el("p", { class: "inf-eyebrow", text: `Consulta · ${referencia(r)}` }),
      el("h1", { text: r.pregunta }),
      el("dl", { class: "inf-ficha" },
        dato("Ley vigente a", fmtLarga(r.fecha)),
        dato("Ámbito", r.municipio_detectado ? `${lugar} · deducido de la pregunta` : lugar),
        dato("Fuentes citadas", String(lista.length)),
        dato("Verificación", nCitas ? `${nCitas} ${nCitas === 1 ? "cita literal" : "citas literales"}${r.revisada ? ", revisadas" : ""}` : "Sin citas"))),
  ];

  let n = 0;
  if (r.motivo_abstencion) {
    partes.push(el("section", { class: "inf-abstencion" },
      el("p", { class: "inf-rotulo", text: "Resultado" }),
      el("h2", { text: "No puedo afirmarlo con una cita." }),
      el("p", { text: ABSTENCION[r.motivo_abstencion] || "" })));
  } else {
    // El resumen es la primera regla vigente y, si ya hay publicado un cambio, su primera
    // afirmación; ninguna se repite debajo.
    const vigentes = r.afirmaciones.filter((a) => !esFutura(a.fragmento));
    const principal = vigentes.find((a) => a.tipo === "regla") || vigentes[0] || r.afirmaciones[0];
    const cambio = principal && !esFutura(principal.fragmento)
      ? r.afirmaciones.find((a) => esFutura(a.fragmento) && a.tipo === "regla") || r.afirmaciones.find((a) => esFutura(a.fragmento))
      : null;
    const ref = (a) => el("p", { class: "inf-ref", text: `[${fuentes.get(a.fragmento.id).n}] ${a.fragmento.cita}` });
    partes.push(el("section", { class: "inf-resumen" },
      el("p", { class: "inf-rotulo", text: "En resumen" }),
      el("p", { class: "inf-resumen__texto", text: principal.texto }), ref(principal),
      cambio ? el("div", { class: "inf-resumen__cambio" },
        el("p", { class: "inf-rotulo", text: `Cambia el ${fmtLarga(cambio.fragmento.vigente_desde)}` }),
        el("p", { class: "inf-resumen__texto", text: cambio.texto }), ref(cambio)) : null));
    for (const g of agrupar(r.afirmaciones.filter((a) => a !== principal && a !== cambio))) {
      partes.push(seccion(++n, g.titulo, ...g.lista.map(afirmacion)));
    }
  }
  // Los avisos de una fuente ya van junto a ella; aquí solo los generales.
  const deFuente = new Set(lista.flatMap(({ f }) => f.avisos || []));
  const generales = r.avisos.filter((a) => !deFuente.has(a));
  if (generales.length) partes.push(seccion(++n, "Ten en cuenta", ...generales.map((a) => el("p", { class: "inf-aviso", text: a }))));
  if (r.preguntas_pendientes.length) {
    partes.push(seccion(++n, "Datos que afinarían la respuesta",
      el("ul", { class: "inf-pendientes" }, ...r.preguntas_pendientes.map((q) => el("li", { text: q })))));
  }
  if (lista.length) {
    partes.push(seccion(++n, "Fuentes citadas", ...lista.map(({ n: k, f, citas }) => {
      const no = f.norma || {};
      const territorio = no.municipio ? `Municipal · ${no.municipio}` : (TERRITORIOS[no.territorio] || "");
      return el("article", { class: "inf-fuente" },
        el("span", { class: "inf-fuente__n", text: String(k) }),
        el("div", {},
          el("p", { class: "inf-fuente__cita", text: f.cita }),
          no.titulo ? el("p", { class: "inf-fuente__norma", text: no.titulo }) : null,
          el("p", { class: "inf-fuente__meta", text: [CLASES[no.clase] || "Norma", territorio,
            esFutura(f) ? `Entra en vigor el ${fmtFecha(f.vigente_desde)}, después de la fecha consultada`
              : `Vigente del ${fmtFecha(f.vigente_desde)} al ${f.vigente_hasta ? fmtFecha(f.vigente_hasta) : "día de hoy"}${f.vigencia_incierta ? " (fecha no publicada)" : ""}`].filter(Boolean).join(" · ") }),
          ...citas.map((c) => el("blockquote", { text: c })),
          ...(f.avisos || []).map((a) => el("p", { class: "inf-aviso", text: a })),
          seguro(f.url) ? el("a", { class: "inf-url", href: seguro(f.url), text: seguro(f.url) }) : null));
    })));
  }

  const ahora = new Date();
  const descartes = (r.descartadas || 0) + (r.no_respaldadas || 0);
  const corregidas = r.afirmaciones.filter((a) => a.corregida).length;
  partes.push(el("section", { class: "inf-metodo" },
    el("p", { class: "inf-rotulo", text: "Cómo se ha elaborado" }),
    el("p", { text: r.motivo_abstencion
      ? "El asistente solo afirma lo que puede probar con una cita literal del texto oficial vigente. "
        + "En esta consulta no ha podido hacerlo, así que no ofrece ninguna respuesta: es preferible a una que no se pueda comprobar."
      : `Un modelo de lenguaje (${r.modelo}) redactó las afirmaciones a partir de las normas vigentes a ${fmtLarga(r.fecha)}. `
        + "Un programa comprobó que cada cita aparece palabra por palabra en el texto oficial"
        + (r.revisada ? " y una segunda revisión, que cada cita respalda lo que se afirma. " : ". ")
        + (descartes === 1 ? "Se descartó 1 afirmación que no superó estas pruebas. " : "")
        + (descartes > 1 ? `Se descartaron ${descartes} afirmaciones que no superaron estas pruebas. ` : "")
        + (corregidas === 1 ? "La revisión reformuló 1 afirmación para ceñirla a su cita. " : "")
        + (corregidas > 1 ? `La revisión reformuló ${corregidas} afirmaciones para ceñirlas a su cita. ` : "")
        + "Las frases son una síntesis; la prueba es la cita, reproducida en el anexo dentro de su texto oficial." }),
    el("p", { class: "inf-legal", text: "Este informe ofrece información sobre la normativa y no constituye asesoramiento jurídico. Antes de decidir, contrasta el texto oficial enlazado o consulta a un profesional." }),
    el("p", { class: "inf-pie", text: `${referencia(r)} · Generado el ${ahora.toLocaleDateString("es-ES", { day: "numeric", month: "long", year: "numeric" })} a las ${ahora.toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit" })} · github.com/alfonmedela/vut-andalucia` })));

  if (lista.length) {
    partes.push(el("section", { class: "inf-anexo" },
      el("p", { class: "inf-eyebrow", text: "Anexo" }),
      el("h2", { text: "Textos oficiales citados" }),
      el("p", { class: "inf-anexo__nota", text: "Cada precepto en la versión vigente a la fecha de la consulta. Lo subrayado es lo que se cita en el informe." }),
      ...lista.map(({ n: k, f, citas }) => el("article", { class: "inf-texto" },
        el("h3", { text: `[${k}] ${f.cita}` }),
        f.encabezado && !f.texto.trimStart().startsWith(f.encabezado.trim()) ? el("p", { class: "inf-texto__enc", text: f.encabezado }) : null,
        el("div", { class: "inf-texto__cuerpo" }, resaltarVarias(f.texto, citas)[0])))));
  }
  return partes;
}

async function exportarInforme() {
  if (!actual) return;
  // El navegador propone el título como nombre del PDF.
  const titulo = document.title;
  const tema = actual.pregunta.replace(/[¿?¡!\\/:*"<>|]/g, "").trim().slice(0, 60);
  document.title = `Informe VUT Andalucía · ${actual.fecha} · ${tema}`;
  window.addEventListener("afterprint", () => { document.title = titulo; }, { once: true });
  await document.fonts.ready;
  window.print();
}

// ── fuentes ────────────────────────────────────────────
async function cargarFuentes() {
  try {
    const d = await api("/api/fuentes");
    const grupos = { andalucia: "Andalucía", municipal: "Municipios", estatal: "España", ue: "Unión Europea" };
    const cont = $("#tabla-fuentes");
    cont.replaceChildren();
    for (const [clave, rotulo] of Object.entries(grupos)) {
      const lista = d.fuentes.filter((f) => f.territorio === clave);
      if (!lista.length) continue;
      lista.sort((a, b) => (a.municipio || "").localeCompare(b.municipio || "", "es") || a.corta.localeCompare(b.corta, "es"));
      cont.append(el("div", {},
        el("h3", { text: `${rotulo} · ${lista.length}` }),
        el("ul", {}, ...lista.map((f) => el("li", {},
          el("span", { class: "corta", text: f.municipio ? `${f.municipio} · ${f.corta}` : f.corta }),
          el("span", { class: "titulo" }, (seguro(f.url) ? el("a", { href: seguro(f.url), target: "_blank", rel: "noopener", text: f.titulo }) : el("span", { text: f.titulo })),
            f.estado ? el("small", { text: f.estado }) : null),
          el("span", { class: "n", text: `${CLASES[f.clase]} · ${f.fragmentos} fragm.` }))))));
    }
  } catch (e) { console.warn("No se pudieron cargar las fuentes", e); }
}

iniciar();
