// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 codo70
/*
 * Beurer BLE - Lovelace card
 *
 *   type: custom:beurer-ble-card
 *   person: person.himmelhof      # optional: HA person, device person id (entry:pid) or name
 *   days: 90                      # optional: history range (30, 90, 365)
 *   show: scale                   # optional: scale | blood_pressure (default: everything)
 *
 * Shows the latest values of one person as tiles and the history of the
 * selected value as a chart. Entities come from the integration
 * (websocket beurer_ble/persons), history from the recorder.
 */

const METRICS = [
  { key: "weight_kg", de: "Gewicht", en: "Weight", unit: "kg", dec: 1, main: true },
  { key: "fat_pct", de: "Körperfett", en: "Body fat", unit: "%", dec: 1 },
  { key: "water_pct", de: "Wasser", en: "Water", unit: "%", dec: 1 },
  { key: "muscle_pct", de: "Muskeln", en: "Muscle", unit: "%", dec: 1 },
  { key: "bone_kg", de: "Knochen", en: "Bone", unit: "kg", dec: 1 },
  { key: "bmi", de: "BMI", en: "BMI", unit: "", dec: 1 },
  { key: "bmr_kcal", de: "Grundumsatz", en: "BMR", unit: "kcal", dec: 0 },
  { key: "amr_kcal", de: "Gesamtumsatz", en: "AMR", unit: "kcal", dec: 0 },
  { key: "systolic", de: "Blutdruck", en: "Blood pressure", unit: "mmHg", dec: 0, bp: true, cat: "blood_pressure", main: true },
  { key: "pulse", de: "Puls", en: "Pulse", unit: "bpm", dec: 0, cat: "blood_pressure" },
];
const BY_KEY = Object.fromEntries(METRICS.map((m) => [m.key, m]));
const RANGES = [30, 90, 365];
const COLORS = { main: "var(--primary-color)", second: "var(--error-color, #db4437)" };

const TEXT = {
  de: { empty: "Noch keine Messwerte – einmal wiegen oder messen.", last: "Letzte Messung", days: "Tage", year: "1 Jahr", nohist: "Kein Verlauf im gewählten Zeitraum." },
  en: { empty: "No readings yet – weigh in or take a reading.", last: "Last reading", days: "days", year: "1 year", nohist: "No history in this range." },
};

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of children) if (c) node.append(c);
  return node;
}

const SVG = "http://www.w3.org/2000/svg";
function svg(tag, attrs = {}) {
  const node = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

function num(state) {
  const v = parseFloat(state);
  return Number.isFinite(v) ? v : null;
}

class BeurerBleCard extends HTMLElement {
  static getConfigElement() {
    return document.createElement("beurer-ble-card-editor");
  }

  static getStubConfig() {
    return { days: 90 };
  }

  setConfig(config) {
    this._config = { days: 90, ...config };
    this._metric = null;
    this._history = null;
    this._historyKey = null;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._render();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    this._lang = (hass.language || "de").startsWith("de") ? "de" : "en";
    if (first || !this._persons) this._loadPersons();
    else this._update();
  }

  getCardSize() {
    return 6;
  }

  _t(key) {
    return TEXT[this._lang || "de"][key];
  }

  async _loadPersons() {
    if (this._loading) return;
    this._loading = true;
    try {
      this._persons = await this._hass.callWS({ type: "beurer_ble/persons" });
    } catch (err) {
      this._error = String(err.message || err);
    }
    this._loading = false;
    this._render();
  }

  _person() {
    const persons = this._persons || [];
    const want = this._config.person;
    if (!want) return persons.find((p) => p.ha_person) || persons[0];
    return persons.find((p) => p.ha_person === want || p.id === want || p.name === want);
  }

  _metricsOf(person) {
    const show = this._config.show;
    return METRICS.filter(
      (m) => person.entities[m.key] && (!show || (m.cat || "scale") === show)
    );
  }

  _update() {
    // Re-render only when one of our entities changed.
    const person = this._person();
    if (!person) return;
    const sig = Object.values(person.entities)
      .map((id) => this._hass.states[id]?.state)
      .join("|");
    if (sig === this._sig) return;
    this._sig = sig;
    this._history = null; // new reading: reload the chart
    this._render();
  }

  _render() {
    if (!this.shadowRoot || !this._config) return;
    const root = this.shadowRoot;
    root.innerHTML = "";
    root.append(el("style", { text: STYLE }));
    const card = el("ha-card");
    root.append(card);

    if (this._error) {
      card.append(el("div", { class: "msg", text: this._error }));
      return;
    }
    if (!this._persons) return;
    const person = this._person();
    if (!person) {
      card.append(el("div", { class: "msg", text: this._t("empty") }));
      return;
    }
    const metrics = this._metricsOf(person);
    if (!metrics.length) {
      card.append(el("div", { class: "msg", text: this._t("empty") }));
      return;
    }
    if (!this._metric || !metrics.some((m) => m.key === this._metric)) {
      this._metric = (metrics.find((m) => m.main) || metrics[0])?.key;
    }

    card.append(this._header(person));
    card.append(this._tiles(person, metrics));
    card.append(this._chartBlock(person));
  }

  _header(person) {
    const show = this._config.show;
    const keys = show === "scale" ? ["weight_kg"] : show === "blood_pressure" ? ["systolic"] : ["weight_kg", "systolic"];
    const stamps = keys
      .map((k) => person.entities[k] && this._hass.states[person.entities[k]]?.last_changed)
      .filter(Boolean)
      .map((s) => new Date(s));
    const last = stamps.length ? new Date(Math.max(...stamps)) : null;
    const when = last
      ? last.toLocaleString(this._hass.language || undefined, { dateStyle: "medium", timeStyle: "short" })
      : "–";
    return el("div", { class: "header" }, [
      el("ha-icon", { icon: show === "blood_pressure" ? "mdi:heart-pulse" : "mdi:scale-bathroom" }),
      el("div", { class: "title", text: this._config.title || person.name }),
      el("div", { class: "when", text: `${this._t("last")}: ${when}` }),
    ]);
  }

  _value(person, key) {
    const id = person.entities[key];
    return id ? num(this._hass.states[id]?.state) : null;
  }

  _tiles(person, metrics) {
    const grid = el("div", { class: "tiles" });
    for (const m of metrics) {
      if (m.key === "diastolic") continue;
      let text;
      if (m.bp) {
        const s = this._value(person, "systolic");
        const d = this._value(person, "diastolic");
        text = s === null ? "–" : `${s}/${d ?? "–"}`;
      } else {
        const v = this._value(person, m.key);
        text = v === null ? "–" : v.toLocaleString(this._hass.language || undefined, {
          minimumFractionDigits: m.dec,
          maximumFractionDigits: m.dec,
        });
      }
      const tile = el(
        "div",
        {
          class: `tile${m.key === this._metric ? " active" : ""}${m.main ? " main" : ""}`,
          title: m[this._lang],
          onclick: () => {
            this._metric = m.key;
            this._render();
          },
          ondblclick: () => this._moreInfo(person.entities[m.key]),
        },
        [
          el("div", { class: "label", text: m[this._lang] }),
          el("div", { class: "value" }, [
            el("span", { text }),
            el("span", { class: "unit", text: m.unit }),
          ]),
        ]
      );
      if (m.main) tile.append(el("div", { class: "delta", "data-key": m.key }));
      grid.append(tile);
    }
    return grid;
  }

  _chartBlock(person) {
    const block = el("div", { class: "chart" });
    const ranges = el("div", { class: "ranges" });
    for (const d of RANGES) {
      ranges.append(
        el("button", {
          class: d === Number(this._config.days) ? "on" : "",
          text: d === 365 ? this._t("year") : `${d} ${this._t("days")}`,
          onclick: () => {
            this._config = { ...this._config, days: d };
            this._history = null;
            this._render();
          },
        })
      );
    }
    block.append(ranges);
    const plot = el("div", { class: "plot" });
    block.append(plot);
    this._drawChart(person, plot);
    return block;
  }

  async _fetchHistory(entityIds, days) {
    const key = `${entityIds.join(",")}|${days}`;
    if (this._history && this._historyKey === key) return this._history;
    const start = new Date(Date.now() - days * 86400000).toISOString();
    const res = await this._hass.callWS({
      type: "history/history_during_period",
      start_time: start,
      entity_ids: entityIds,
      minimal_response: true,
      no_attributes: true,
      significant_changes_only: false,
    });
    const out = {};
    for (const id of entityIds) {
      out[id] = (res[id] || [])
        .map((p) => ({ t: (p.lu || p.lc) * 1000, v: num(p.s) }))
        .filter((p) => p.v !== null && p.t);
    }
    this._history = out;
    this._historyKey = key;
    return out;
  }

  async _drawChart(person, plot) {
    const metric = BY_KEY[this._metric];
    if (!metric) return;
    const ids = metric.bp
      ? [person.entities.systolic, person.entities.diastolic].filter(Boolean)
      : [person.entities[metric.key]];
    let hist;
    try {
      hist = await this._fetchHistory(ids, Number(this._config.days) || 90);
    } catch (err) {
      plot.append(el("div", { class: "msg", text: String(err.message || err) }));
      return;
    }
    const series = ids.map((id, i) => ({ pts: hist[id] || [], color: i ? COLORS.second : COLORS.main }));
    this._showDelta(metric, series[0].pts);
    const all = series.flatMap((s) => s.pts);
    if (all.length < 1) {
      plot.append(el("div", { class: "msg", text: this._t("nohist") }));
      return;
    }

    const W = 600, H = 170, L = 40, R = 10, T = 10, B = 22;
    const t0 = Date.now() - (Number(this._config.days) || 90) * 86400000;
    const t1 = Date.now();
    let lo = Math.min(...all.map((p) => p.v));
    let hi = Math.max(...all.map((p) => p.v));
    const pad = Math.max((hi - lo) * 0.15, metric.dec ? 0.5 : 2);
    lo -= pad;
    hi += pad;
    const x = (t) => L + ((t - t0) / (t1 - t0)) * (W - L - R);
    const y = (v) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);

    const root = svg("svg", { viewBox: `0 0 ${W} ${H}` });
    for (let i = 0; i <= 3; i++) {
      const v = lo + ((hi - lo) * i) / 3;
      root.append(svg("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), class: "grid" }));
      const label = svg("text", { x: L - 6, y: y(v) + 4, class: "axis", "text-anchor": "end" });
      label.textContent = v.toFixed(metric.dec ? 1 : 0);
      root.append(label);
    }
    const fmt = (t) => new Date(t).toLocaleDateString(this._hass.language || undefined, { day: "numeric", month: "short" });
    for (const [t, anchor] of [[t0, "start"], [t1, "end"]]) {
      const label = svg("text", { x: x(t), y: H - 6, class: "axis", "text-anchor": anchor });
      label.textContent = fmt(t);
      root.append(label);
    }
    if (metric.bp && series.length === 2) {
      // Blood pressure: fill the band between systolic (top) and diastolic (bottom).
      const top = series[0].pts.filter((p) => p.t >= t0);
      const bottom = series[1].pts.filter((p) => p.t >= t0);
      if (top.length && bottom.length) {
        const outline = [...top, ...[...bottom].reverse()].map((p) => `${x(p.t)},${y(p.v)}`).join(" ");
        root.append(svg("polygon", { points: outline, class: "band" }));
      }
    }
    for (const s of series) {
      const pts = s.pts.filter((p) => p.t >= t0);
      if (pts.length > 1) {
        root.append(
          svg("polyline", {
            points: pts.map((p) => `${x(p.t)},${y(p.v)}`).join(" "),
            class: "line",
            style: `stroke:${s.color}`,
          })
        );
      }
      for (const p of pts) {
        const dot = svg("circle", { cx: x(p.t), cy: y(p.v), r: 3.5, style: `fill:${s.color}` });
        const tip = svg("title");
        tip.textContent = `${fmt(p.t)}: ${p.v} ${metric.unit}`;
        dot.append(tip);
        root.append(dot);
      }
    }
    plot.append(root);
  }

  _showDelta(metric, pts) {
    if (!metric.main) return;
    const node = this.shadowRoot.querySelector(`.delta[data-key="${metric.key}"]`);
    if (!node || pts.length < 2) return;
    const diff = pts[pts.length - 1].v - pts[pts.length - 2].v;
    if (!diff) return;
    node.textContent = `${diff > 0 ? "▲" : "▼"} ${Math.abs(diff).toFixed(metric.dec)} ${metric.unit}`;
    node.className = `delta ${diff > 0 ? "up" : "down"}`;
  }

  _moreInfo(entityId) {
    if (!entityId) return;
    this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId }, bubbles: true, composed: true }));
  }
}

class BeurerBleCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = config;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._persons) {
      hass.callWS({ type: "beurer_ble/persons" }).then((p) => {
        this._persons = p;
        this._render();
      });
    }
    if (this._form) this._form.hass = hass;
  }

  _render() {
    if (!this._hass || !this._config) return;
    const options = (this._persons || []).map((p) => ({ value: p.ha_person || p.id, label: p.name }));
    const de = (this._hass.language || "de").startsWith("de");
    const form = document.createElement("ha-form");
    form.hass = this._hass;
    form.data = this._config;
    form.schema = [
      { name: "person", selector: { select: { options, mode: "dropdown" } } },
      {
        name: "show",
        selector: {
          select: {
            mode: "dropdown",
            options: [
              { value: "scale", label: de ? "Waage" : "Scale" },
              { value: "blood_pressure", label: de ? "Blutdruck" : "Blood pressure" },
            ],
          },
        },
      },
      { name: "title", selector: { text: {} } },
      {
        name: "days",
        selector: { select: { options: RANGES.map((d) => ({ value: String(d), label: String(d) })), mode: "dropdown" } },
      },
    ];
    const labels = de
      ? { person: "Person", show: "Anzeigen (leer = alles)", title: "Titel (optional)", days: "Verlauf in Tagen" }
      : { person: "Person", show: "Show (empty = everything)", title: "Title (optional)", days: "History in days" };
    form.computeLabel = (s) => labels[s.name] || s.name;
    form.addEventListener("value-changed", (ev) => {
      this._config = ev.detail.value;
      this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: this._config }, bubbles: true, composed: true }));
    });
    this.innerHTML = "";
    this.append(form);
    this._form = form;
  }
}

const STYLE = `
  ha-card { padding: 16px; }
  .msg { color: var(--secondary-text-color); padding: 8px 0; }
  .header { display: grid; grid-template-columns: auto 1fr; column-gap: 10px; align-items: center; margin-bottom: 12px; }
  .header ha-icon { grid-row: span 2; color: var(--primary-color); --mdc-icon-size: 32px; }
  .title { font-size: 1.25em; font-weight: 600; }
  .when { font-size: 0.85em; color: var(--secondary-text-color); }
  .tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(96px, 1fr)); gap: 8px; }
  .tile { border: 1px solid var(--divider-color); border-radius: 12px; padding: 8px 10px; cursor: pointer; user-select: none; }
  .tile.active { border-color: var(--primary-color); box-shadow: 0 0 0 1px var(--primary-color) inset; }
  .tile.main { grid-column: span 2; }
  .tile.main .value { font-size: 1.9em; }
  .label { font-size: 0.8em; color: var(--secondary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .value { font-size: 1.25em; font-weight: 600; white-space: nowrap; }
  .unit { font-size: 0.6em; font-weight: 400; margin-left: 3px; color: var(--secondary-text-color); }
  .delta { font-size: 0.8em; }
  .delta.up { color: var(--warning-color, #ff9800); }
  .delta.down { color: var(--success-color, #43a047); }
  .chart { margin-top: 14px; }
  .ranges { display: flex; gap: 6px; justify-content: flex-end; margin-bottom: 4px; }
  .ranges button { font: inherit; font-size: 0.8em; border: 1px solid var(--divider-color); background: none; color: var(--primary-text-color); border-radius: 12px; padding: 2px 10px; cursor: pointer; }
  .ranges button.on { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
  .plot svg { width: 100%; height: auto; display: block; }
  .plot .grid { stroke: var(--divider-color); stroke-width: 1; }
  .plot .axis { fill: var(--secondary-text-color); font-size: 11px; }
  .plot .band { fill: var(--primary-color); fill-opacity: 0.18; stroke: none; }
  .plot .line { fill: none; stroke-width: 2.5; stroke-linejoin: round; vector-effect: non-scaling-stroke; }
`;

if (!customElements.get("beurer-ble-card")) customElements.define("beurer-ble-card", BeurerBleCard);
if (!customElements.get("beurer-ble-card-editor")) customElements.define("beurer-ble-card-editor", BeurerBleCardEditor);

window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === "beurer-ble-card")) {
  window.customCards.push({
    type: "beurer-ble-card",
    name: "Beurer BLE",
    description: "Gewicht, Körperwerte und Blutdruck einer Person mit Verlauf",
    preview: false,
  });
}
