/* Since card for Home Assistant.
 * Shows how long it has been since each tracked event, with due/overdue colours.
 * Data comes only from the Since integration's websocket API, which refuses
 * any user who isn't allowed in the integration's settings.
 */
(() => {
  const VERSION = "1.0.0";
  const UNITS = ["hours", "days", "weeks", "months", "years"];
  const UNIT_SINGULAR = { hours: "hour", days: "day", weeks: "week", months: "month", years: "year" };

  const esc = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

  // ---- date maths (mirrors the integration) -------------------------------

  function addMonthsClamped(date, months) {
    const d = new Date(date.getTime());
    const day = d.getDate();
    d.setDate(1);
    d.setMonth(d.getMonth() + months);
    const lastDay = new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate();
    d.setDate(Math.min(day, lastDay));
    return d;
  }

  function addInterval(date, interval) {
    const { amount, unit } = interval;
    if (unit === "hours") return new Date(date.getTime() + amount * 3600e3);
    if (unit === "days" || unit === "weeks") {
      const d = new Date(date.getTime());
      d.setDate(d.getDate() + amount * (unit === "weeks" ? 7 : 1));
      return d;
    }
    return addMonthsClamped(date, amount * (unit === "years" ? 12 : 1));
  }

  /** "3 days, 4 hours" style text between two dates, at most `maxUnits` units. */
  function duration(from, to, maxUnits = 2) {
    if (to < from) [from, to] = [to, from];
    const parts = [];
    let cursor = new Date(from.getTime());
    let months = (to.getFullYear() - cursor.getFullYear()) * 12 + (to.getMonth() - cursor.getMonth());
    while (months > 0 && addMonthsClamped(cursor, months) > to) months--;
    if (months > 0) cursor = addMonthsClamped(cursor, months);
    const years = Math.floor(months / 12);
    months %= 12;
    let rest = Math.floor((to - cursor) / 60000); // minutes
    const weeks = Math.floor(rest / 10080); rest -= weeks * 10080;
    const days = Math.floor(rest / 1440); rest -= days * 1440;
    const hours = Math.floor(rest / 60); rest -= hours * 60;
    const units = [[years, "year"], [months, "month"], [weeks, "week"], [days, "day"], [hours, "hour"], [rest, "minute"]];
    for (const [n, word] of units) {
      if (parts.length >= maxUnits) break;
      if (n > 0) parts.push(plural(n, word));
      else if (parts.length) break; // keep units adjacent: "1 month" not "1 month, 5 minutes"
    }
    return parts.join(", ") || "0 minutes";
  }

  function ago(date, now) {
    const s = (now - date) / 1000;
    if (s < 0) return "In " + duration(now, date);
    if (s < 60) return "Just now";
    return duration(date, now) + " ago";
  }

  function lastOf(ev) {
    let best = null;
    for (const o of ev.occurrences) {
      const d = new Date(o.date);
      if (!best || d > best) best = d;
    }
    return best;
  }

  function dueOf(ev) {
    const last = lastOf(ev);
    return ev.interval && last ? addInterval(last, ev.interval) : null;
  }

  function statusOf(ev, now) {
    const last = lastOf(ev);
    const due = dueOf(ev);
    if (!last || !due) return "none";
    if (now >= due) return "overdue";
    const total = due - last;
    const remaining = due - now;
    return remaining <= Math.max(total * 0.15, 3600e3) ? "soon" : "ok";
  }

  function dueText(ev, now) {
    const due = dueOf(ev);
    if (!due) return "";
    const delta = (due - now) / 1000;
    if (delta <= 0) return -delta < 60 ? "Due now" : "Overdue " + duration(due, now, 1);
    if (delta < 60) return "Due in under a minute";
    return "Due in " + duration(now, due, 1);
  }

  function intervalSummary(iv) {
    return iv.amount === 1 ? `every ${UNIT_SINGULAR[iv.unit]}` : `every ${iv.amount} ${iv.unit}`;
  }

  function kindSummary(ev) {
    if (!ev.interval) return "Time since only";
    return (ev.notifies ? "Reminder, " : "Recommended ") + intervalSummary(ev.interval);
  }

  function averageGap(ev) {
    const ds = ev.occurrences.map((o) => new Date(o.date).getTime()).sort((a, b) => a - b);
    if (ds.length < 2) return null;
    return (ds[ds.length - 1] - ds[0]) / (ds.length - 1);
  }

  function gapText(ms) {
    const mins = Math.round(ms / 60000);
    const d = Math.floor(mins / 1440), h = Math.floor((mins % 1440) / 60), m = mins % 60;
    const parts = [];
    if (d) parts.push(plural(d, "day"));
    if (h) parts.push(plural(h, "hour"));
    if (m && !d) parts.push(plural(m, "minute"));
    return parts.slice(0, 2).join(", ") || "under a minute";
  }

  const fmtDate = (d) =>
    d.toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit" });

  /** Value for <input type="datetime-local"> in local time. */
  function toLocalInput(d) {
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
  }
  const fromLocalInput = (v) => new Date(v).toISOString();

  const STATUS_ORDER = { overdue: 0, soon: 1, ok: 2, none: 3 };

  // ---- the card ------------------------------------------------------------

  class SinceCard extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._events = null;
      this._error = null;
      this._view = { name: "list" };
      this._unsub = null;
      this._subscribing = false;
    }

    setConfig(config) {
      this._config = { title: "Since", due_soon_color: "#ff9500", overdue_color: "#ff3b30", ...config };
      this._render();
    }

    set hass(hass) {
      const first = !this._hass;
      this._hass = hass;
      if (first || (!this._unsub && !this._subscribing && this.isConnected)) this._subscribe();
    }

    connectedCallback() {
      if (this._hass && !this._unsub) this._subscribe();
      this._timer = setInterval(() => this._tick(), 30000);
    }

    disconnectedCallback() {
      clearInterval(this._timer);
      if (this._unsub) {
        this._unsub.then((u) => u()).catch(() => {});
        this._unsub = null;
      }
    }

    getCardSize() {
      return 2 + (this._events ? Math.min(this._events.length, 8) : 1);
    }

    static getStubConfig() {
      return { title: "Since" };
    }

    async _subscribe() {
      if (!this._hass || this._subscribing) return;
      this._subscribing = true;
      try {
        const p = this._hass.connection.subscribeMessage((data) => {
          this._events = data.events;
          this._error = null;
          this._onData();
        }, { type: "since/subscribe" });
        this._unsub = p;
        await p;
      } catch (err) {
        this._unsub = null;
        this._error =
          err && err.code === "unauthorized"
            ? "Your user doesn't have access to Since."
            : err && err.code === "unknown_command"
              ? "The Since integration isn't set up yet."
              : (err && err.message) || "Couldn't load Since.";
        this._render();
      } finally {
        this._subscribing = false;
      }
    }

    _onData() {
      // Don't wipe a form the user is typing in; refresh after they finish.
      if (this._view.name === "form" || this._view.name === "entry") {
        this._pendingRender = true;
        return;
      }
      if (this._view.name === "detail" && !this._find(this._view.id)) this._view = { name: "list" };
      this._render();
    }

    _tick() {
      if (this._view.name === "list" || this._view.name === "detail") this._render();
    }

    _find(id) {
      return (this._events || []).find((e) => e.id === id);
    }

    async _ws(msg) {
      try {
        return await this._hass.callWS(msg);
      } catch (err) {
        this._flash = (err && err.message) || "Something went wrong.";
        this._render();
        throw err;
      }
    }

    _go(view) {
      this._view = view;
      this._armedAct = null;
      this._pendingRender = false;
      this._render();
    }

    _colour(status) {
      return {
        overdue: this._config.overdue_color,
        soon: this._config.due_soon_color,
        ok: "var(--success-color, #34c759)",
        none: "var(--disabled-text-color, #9e9e9e)",
      }[status];
    }

    // ---- rendering ---------------------------------------------------------

    _render() {
      if (!this._config) return;
      const root = this.shadowRoot;
      const scroll = root.querySelector(".body")?.scrollTop;
      let body;
      if (this._error) body = `<div class="empty">${esc(this._error)}</div>`;
      else if (!this._events) body = `<div class="empty">Loading…</div>`;
      else if (this._view.name === "detail") body = this._renderDetail();
      else if (this._view.name === "form") body = this._renderForm();
      else if (this._view.name === "entry") body = this._renderEntry();
      else body = this._renderList();
      const flash = this._flash ? `<div class="flash" role="alert">${esc(this._flash)}</div>` : "";
      this._flash = null;
      root.innerHTML = `<style>${STYLE}</style><ha-card>${flash}${body}</ha-card>`;
      const b = root.querySelector(".body");
      if (b && scroll) b.scrollTop = scroll;
      this._bind();
    }

    _header(title, { back = false, actions = "" } = {}) {
      return `<div class="header">
        ${back ? `<button class="icon" data-act="back" aria-label="Back">‹</button>` : ""}
        <div class="title">${esc(title)}</div>
        <div class="actions">${actions}</div>
      </div>`;
    }

    _renderList() {
      const now = new Date();
      const rows = this._events
        .map((ev) => ({ ev, status: statusOf(ev, now), last: lastOf(ev), due: dueOf(ev) }))
        .sort((a, b) => {
          const s = STATUS_ORDER[a.status] - STATUS_ORDER[b.status];
          if (s) return s;
          if (a.due && b.due) return a.due - b.due;
          if (a.last && b.last) return b.last - a.last;
          return a.ev.name.localeCompare(b.ev.name);
        });
      const header = this._header(this._config.title, {
        actions: `<button class="icon" data-act="new" aria-label="Add event">＋</button>`,
      });
      if (!rows.length) {
        return `${header}<div class="empty">Nothing tracked yet.<br><button class="primary" data-act="new">Add an event</button></div>`;
      }
      return `${header}<div class="body list">${rows
        .map(({ ev, status, last }) => {
          const due = dueText(ev, now);
          return `<div class="row" data-open="${esc(ev.id)}">
            <span class="dot" style="background:${this._colour(status)}"></span>
            <div class="main">
              <div class="name">${esc(ev.name)}</div>
              <div class="sub">${last ? esc(ago(last, now)) : "Never logged"}${
                due ? ` · <span style="color:${status === "ok" ? "inherit" : this._colour(status)}">${esc(due)}</span>` : ""
              }</div>
            </div>
            <button class="log" data-log="${esc(ev.id)}">Log</button>
          </div>`;
        })
        .join("")}</div>`;
    }

    _renderDetail() {
      const ev = this._find(this._view.id);
      if (!ev) return this._renderList();
      const now = new Date();
      const last = lastOf(ev);
      const status = statusOf(ev, now);
      const avg = averageGap(ev);
      const due = dueText(ev, now);
      const header = this._header(ev.name, {
        back: true,
        actions: `<button class="text" data-act="edit">Edit</button>`,
      });
      const facts = [
        ["Type", kindSummary(ev)],
        ["Last time", last ? `${ago(last, now)}<div class="muted">${esc(fmtDate(last))}</div>` : "Never logged"],
      ];
      if (due) facts.push(["Next", `<span style="color:${status === "ok" ? "inherit" : this._colour(status)}">${esc(due)}</span><div class="muted">${esc(fmtDate(dueOf(ev)))}</div>`]);
      if (avg) facts.push(["Average gap", esc(gapText(avg))]);
      facts.push(["Times logged", String(ev.occurrences.length)]);
      return `${header}<div class="body">
        <div class="facts">${facts.map(([k, v]) => `<div class="k">${k}</div><div class="v">${v}</div>`).join("")}</div>
        <div class="buttons">
          <button class="primary" data-log="${esc(ev.id)}">Log it now</button>
          <button data-act="add-past">Add with date or note</button>
        </div>
        <div class="section">History</div>
        ${
          ev.occurrences.length
            ? ev.occurrences
                .map(
                  (o) => `<div class="row hist" data-entry="${esc(o.id)}">
              <div class="main"><div>${esc(fmtDate(new Date(o.date)))}</div>${o.note ? `<div class="sub">${esc(o.note)}</div>` : ""}</div>
              <span class="chev">›</span></div>`
                )
                .join("")
            : `<div class="muted pad">No entries yet.</div>`
        }
      </div>`;
    }

    _renderForm() {
      const ev = this._view.id ? this._find(this._view.id) : null;
      const iv = ev?.interval;
      const kind = !iv ? "none" : ev.notifies ? "reminder" : "recommended";
      return `${this._header(ev ? "Edit event" : "New event", { back: true })}
      <form class="body form" id="f">
        <label>Name<input name="name" required maxlength="200" value="${esc(ev?.name ?? "")}" autocomplete="off"></label>
        <label>Type
          <select name="kind">
            <option value="none" ${kind === "none" ? "selected" : ""}>Time since only</option>
            <option value="recommended" ${kind === "recommended" ? "selected" : ""}>Time since, with a recommended interval</option>
            <option value="reminder" ${kind === "reminder" ? "selected" : ""}>Reminder (sends a notification when due)</option>
          </select>
        </label>
        <div class="interval" ${kind === "none" ? "hidden" : ""}>
          <span>Every</span>
          <input name="amount" type="number" min="1" max="1000" value="${iv?.amount ?? 1}">
          <select name="unit">${UNITS.map((u) => `<option value="${u}" ${(iv?.unit ?? "weeks") === u ? "selected" : ""}>${u}</option>`).join("")}</select>
        </div>
        ${ev ? "" : `<label class="check"><input type="checkbox" name="log_now"> It happened just now</label>`}
        <div class="buttons">
          <button class="primary" type="submit">${ev ? "Save" : "Add"}</button>
          ${ev ? `<button type="button" class="danger" data-act="delete-event" data-label="Delete event">Delete event</button>` : ""}
        </div>
      </form>`;
    }

    _renderEntry() {
      const ev = this._find(this._view.id);
      if (!ev) return this._renderList();
      const o = this._view.entryId ? ev.occurrences.find((x) => x.id === this._view.entryId) : null;
      const date = o ? new Date(o.date) : new Date();
      return `${this._header(o ? "Edit entry" : "Add entry", { back: true })}
      <form class="body form" id="f">
        <label>When<input name="date" type="datetime-local" required value="${toLocalInput(date)}"></label>
        <label>Note<textarea name="note" rows="3" maxlength="2000">${esc(o?.note ?? "")}</textarea></label>
        <div class="buttons">
          <button class="primary" type="submit">Save</button>
          ${o ? `<button type="button" class="danger" data-act="delete-entry" data-label="Delete entry">Delete entry</button>` : ""}
        </div>
      </form>`;
    }

    // ---- interaction -------------------------------------------------------

    _bind() {
      const root = this.shadowRoot;
      root.querySelectorAll("[data-log]").forEach((el) =>
        el.addEventListener("click", async (e) => {
          e.stopPropagation();
          el.disabled = true;
          el.textContent = "✓";
          await this._ws({ type: "since/log", event_id: el.dataset.log }).catch(() => {});
        })
      );
      root.querySelectorAll("[data-open]").forEach((el) =>
        el.addEventListener("click", () => this._go({ name: "detail", id: el.dataset.open }))
      );
      root.querySelectorAll("[data-entry]").forEach((el) =>
        el.addEventListener("click", () => this._go({ name: "entry", id: this._view.id, entryId: el.dataset.entry }))
      );
      root.querySelectorAll("[data-act]").forEach((el) =>
        el.addEventListener("click", (e) => {
          e.preventDefault();
          this._action(el.dataset.act);
        })
      );
      const kind = root.querySelector("select[name=kind]");
      if (kind) kind.addEventListener("change", () => (root.querySelector(".interval").hidden = kind.value === "none"));
      const form = root.querySelector("#f");
      if (form) form.addEventListener("submit", (e) => { e.preventDefault(); this._submit(form); });
    }

    _back() {
      const v = this._view;
      if ((v.name === "entry" || (v.name === "form" && v.id)) && this._find(v.id)) this._go({ name: "detail", id: v.id });
      else this._go({ name: "list" });
    }

    /** Two-tap confirmation: the first tap arms the button, a second tap within 4 s confirms. */
    _armed(act, label) {
      const btn = this.shadowRoot.querySelector(`[data-act="${act}"]`);
      if (this._armedAct === act) {
        this._armedAct = null;
        clearTimeout(this._armTimer);
        return true;
      }
      this._armedAct = act;
      if (btn) btn.textContent = label;
      clearTimeout(this._armTimer);
      this._armTimer = setTimeout(() => {
        this._armedAct = null;
        if (btn && btn.isConnected) btn.textContent = btn.dataset.label;
      }, 4000);
      return false;
    }

    async _action(act) {
      const v = this._view;
      if (act === "back") return this._back();
      if (act === "new") return this._go({ name: "form" });
      if (act === "edit") return this._go({ name: "form", id: v.id });
      if (act === "add-past") return this._go({ name: "entry", id: v.id });
      if (act === "delete-event") {
        const ev = this._find(v.id);
        if (!ev || !this._armed(act, "Tap again to delete it and its history")) return;
        await this._ws({ type: "since/event/delete", event_id: v.id });
        return this._go({ name: "list" });
      }
      if (act === "delete-entry") {
        if (!this._armed(act, "Tap again to delete")) return;
        await this._ws({ type: "since/occurrence/delete", event_id: v.id, occurrence_id: v.entryId });
        return this._go({ name: "detail", id: v.id });
      }
    }

    async _submit(form) {
      const f = new FormData(form);
      const v = this._view;
      const btn = form.querySelector("button[type=submit]");
      btn.disabled = true;
      try {
        if (v.name === "form") {
          const kind = f.get("kind");
          const interval = kind === "none" ? null : { amount: Math.max(1, parseInt(f.get("amount"), 10) || 1), unit: f.get("unit") };
          const body = { name: String(f.get("name")).trim(), interval, notifies: kind === "reminder" };
          if (!body.name) return;
          if (v.id) {
            await this._ws({ type: "since/event/update", event_id: v.id, ...body });
            this._go({ name: "detail", id: v.id });
          } else {
            const res = await this._ws({ type: "since/event/create", ...body, log_now: f.get("log_now") === "on" });
            this._go({ name: "detail", id: res.id });
          }
        } else if (v.name === "entry") {
          const date = fromLocalInput(f.get("date"));
          const note = String(f.get("note") || "");
          if (v.entryId) await this._ws({ type: "since/occurrence/update", event_id: v.id, occurrence_id: v.entryId, date, note });
          else await this._ws({ type: "since/log", event_id: v.id, date, note });
          this._go({ name: "detail", id: v.id });
        }
      } catch (e) {
        btn.disabled = false;
      }
    }
  }

  const STYLE = `
    :host { display: block; }
    ha-card { overflow: hidden; color: var(--primary-text-color); }
    button { font: inherit; cursor: pointer; border: 1px solid var(--divider-color, #ccc); background: transparent;
      color: var(--primary-text-color); border-radius: 8px; padding: 6px 12px; }
    button:disabled { opacity: .6; }
    button.primary { background: var(--primary-color, #03a9f4); color: var(--text-primary-color, #fff); border-color: transparent; }
    button.danger { color: var(--error-color, #db4437); border-color: var(--error-color, #db4437); }
    button.icon { border: none; font-size: 22px; line-height: 1; padding: 4px 8px; color: var(--primary-color, #03a9f4); }
    button.text { border: none; color: var(--primary-color, #03a9f4); }
    .header { display: flex; align-items: center; gap: 4px; padding: 12px 12px 4px 16px; min-height: 40px; }
    .header .title { flex: 1; font-size: 20px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .body { padding: 4px 16px 16px; max-height: 70vh; overflow-y: auto; }
    .empty { padding: 24px 16px; text-align: center; color: var(--secondary-text-color); }
    .empty button { margin-top: 12px; }
    .row { display: flex; align-items: center; gap: 12px; padding: 10px 0; border-bottom: 1px solid var(--divider-color, #e0e0e0); cursor: pointer; }
    .row:last-child { border-bottom: none; }
    .dot { width: 12px; height: 12px; border-radius: 50%; flex: none; }
    .main { flex: 1; min-width: 0; }
    .name { font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .sub, .muted { color: var(--secondary-text-color); font-size: 14px; }
    .pad { padding: 8px 0; }
    .log { flex: none; }
    .chev { color: var(--secondary-text-color); font-size: 20px; }
    .facts { display: grid; grid-template-columns: auto 1fr; gap: 8px 16px; margin: 8px 0 16px; }
    .facts .k { color: var(--secondary-text-color); }
    .buttons { display: flex; flex-wrap: wrap; gap: 8px; margin: 8px 0; }
    .section { margin-top: 16px; font-weight: 500; color: var(--secondary-text-color); text-transform: uppercase; font-size: 12px; letter-spacing: .05em; }
    .form label { display: flex; flex-direction: column; gap: 4px; margin: 12px 0; color: var(--secondary-text-color); font-size: 14px; }
    .form label.check { flex-direction: row; align-items: center; gap: 8px; color: var(--primary-text-color); font-size: 16px; }
    .form input:not([type=checkbox]), .form select, .form textarea { font: inherit; font-size: 16px; padding: 8px; border-radius: 8px;
      border: 1px solid var(--divider-color, #ccc); background: var(--card-background-color, #fff); color: var(--primary-text-color); }
    .interval { display: flex; align-items: center; gap: 8px; }
    .interval[hidden] { display: none; }
    .interval input { width: 5em; }
    .flash { margin: 12px 16px 0; padding: 8px 12px; border-radius: 8px; color: var(--error-color, #db4437);
      border: 1px solid var(--error-color, #db4437); font-size: 14px; }
  `;

  if (!customElements.get("since-card")) customElements.define("since-card", SinceCard);
  window.customCards = window.customCards || [];
  if (!window.customCards.some((c) => c.type === "since-card")) {
    window.customCards.push({
      type: "since-card",
      name: "Since",
      description: "Private time-since and reminder tracker",
      preview: false,
    });
  }
  console.info(`%c SINCE-CARD %c ${VERSION} `, "background:#03a9f4;color:#fff", "");
})();
