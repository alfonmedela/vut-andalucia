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
const seguro = (url) => (typeof url === "string" && /^https:\/\//.test(url) ? url : null);

let municipios = [];
let actual = null; // última respuesta

// ── arranque ───────────────────────────────────────────
async function iniciar() {
  $("#fecha").value = hoyISO();
  document.querySelectorAll(".chip").forEach((c) => c.addEventListener("click", () => {
    $("#pregunta").value = c.dataset.p;
    $("#municipio").value = c.dataset.m || "";
    $("#fecha").value = c.dataset.f || hoyISO();
    enviar();
  }));
  $("#formulario").addEventListener("submit", (e) => { e.preventDefault(); enviar(); });
  $("#pregunta").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); enviar(); }
  });
  try {
    const [estado, lista] = await Promise.all([api("/api/estado"), api("/api/municipios")]);
    $('[data-cifra="fuentes"]').textContent = estado.fuentes.toLocaleString("es-ES");
    $('[data-cifra="fragmentos"]').textContent = estado.fragmentos.toLocaleString("es-ES");
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
  const pregunta = $("#pregunta").value.trim();
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

function marcarPaso(paso) {
  const orden = ["buscar", "redactar", "verificar"];
  const lista = $("#pasos");
  lista.classList.remove("oculto");
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
  $("#pasos").classList.add("oculto");
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

  for (const tipo of Object.keys(ROTULOS)) {
    const grupo = r.afirmaciones.filter((a) => a.tipo === tipo);
    if (!grupo.length) continue;
    const varios = r.afirmaciones.some((a) => a.tipo !== "regla");
    const sec = el("section", { class: `grupo grupo--${tipo}` }, varios ? el("h3", { text: ROTULOS[tipo] }) : null);
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
  $("#pie-respuesta").textContent = pie.join(" · ");

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
  $("#pasos").classList.add("oculto");
  $("#respuesta").replaceChildren(el("div", { class: "tarjeta" }, el("p", { text: mensaje })));
  const d = $("#consultadas");
  d.open = true;
  const primero = d.querySelector("button");
  if (primero && window.matchMedia("(min-width: 961px)").matches) primero.click();
}

function pintarError(mensaje) {
  $("#pasos").classList.add("oculto");
  $("#respuesta").replaceChildren(el("div", { class: "tarjeta tarjeta--error", role: "alert" },
    el("strong", { text: "No se pudo completar la respuesta. " }), mensaje));
}

// ── panel de cita ──────────────────────────────────────
function resaltar(texto, cita) {
  // Devuelve nodos con la cita marcada; tolera mayúsculas, espacios, comillas y puntos suspensivos en los extremos.
  const frag = document.createDocumentFragment();
  if (!cita) { frag.append(texto); return [frag, false]; }
  const limpia = cita.replace(/^[\s«“"'(\[.…]+|[\s»”"')\].…]+$/g, "");
  const palabras = limpia.split(/\s+/).filter(Boolean).map((p) => p.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (!palabras.length) { frag.append(texto); return [frag, false]; }
  const m = new RegExp(palabras.join("[\\s«»“”\"']+"), "i").exec(texto);
  if (!m) { frag.append(texto); return [frag, false]; }
  frag.append(texto.slice(0, m.index), el("mark", { text: m[0] }), texto.slice(m.index + m[0].length));
  return [frag, true];
}

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
      el("span", { class: "etiqueta", text: `${fmtFecha(f.vigente_desde)} → ${fmtFecha(f.vigente_hasta)}${f.vigencia_incierta ? " (fecha no publicada)" : ""}` })),
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
