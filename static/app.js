/* --------------------------------------------------------------------- *
 * Who Stays On? — vanilla JS frontend                                   *
 * No external dependencies; talks to /api/roster on the same origin.    *
 * --------------------------------------------------------------------- */

(function () {
  "use strict";

  // --- DOM refs -------------------------------------------------------- //
  const els = {
    btnSample:  document.getElementById("btn-load-sample"),
    btnSolve:   document.getElementById("btn-solve"),
    btnPrint:   document.getElementById("btn-print"),
    status:     document.getElementById("solver-status"),
    elapsed:    document.getElementById("solver-elapsed"),
    input:      document.getElementById("input-json"),
    inputErr:   document.getElementById("input-error"),
    results:    document.getElementById("results-card"),
    infeas:     document.getElementById("infeasible-banner"),
    infeasList: document.getElementById("infeasibility-reasons"),
    kpiValue:   document.getElementById("kpi-value"),
    kpiFuel:    document.getElementById("kpi-fuel"),
    kpiStatus:  document.getElementById("kpi-status"),
    fuelChart:  document.getElementById("fuel-chart"),
    rosterTbl:  document.getElementById("roster-table"),
    reasons:    document.getElementById("dark-reason-list"),
  };

  // --- sample embedded so it works offline even without /api/roster ----- //
  const SAMPLE = {
    _story:
      "Shapla Shopping Complex, Thursday. DESCO load shedding 12:00-16:00 and 17:00-20:00. 20 kW generator, 17 litres of diesel, twelve shops.",
    grid_available: [
      true, true, false, false, false, false, true, false, false, false,
    ],
    generator_capacity_kw: 20,
    fuel_budget_litres: 17.0,
    fuel_table: [
      { load_kw: 7, litres_per_hour: 1.2 },
      { load_kw: 14, litres_per_hour: 2.0 },
      { load_kw: 20, litres_per_hour: 2.8 },
    ],
    max_consecutive_dark: 2,
    shops: [
      { id: "pharmacy", kw: 3, type: "mandatory",
        value_per_slot: [6,6,6,6,6,6,6,6,6,6] },
      { id: "atm_booth", kw: 2, type: "mandatory",
        value_per_slot: [4,4,4,4,4,4,4,4,4,4] },
      { id: "tailor_jamal", kw: 4, type: "normal",
        value_per_slot: [2,2,3,3,4,4,4,4,5,5] },
      { id: "mobile_shop", kw: 3, type: "normal",
        value_per_slot: [1,1,2,2,2,3,4,6,8,8] },
      { id: "photocopy", kw: 2, type: "normal",
        value_per_slot: [3,3,5,5,4,3,2,2,1,1] },
      { id: "tea_stall", kw: 1, type: "normal",
        value_per_slot: [2,2,3,3,3,2,2,3,3,3] },
      { id: "beauty_salon", kw: 4, type: "normal",
        value_per_slot: [1,1,2,3,4,5,5,4,3,2] },
      { id: "grocery_fridge", kw: 3, type: "normal",
        value_per_slot: [3,3,3,3,3,3,4,5,5,4] },
      { id: "electronics", kw: 5, type: "normal",
        value_per_slot: [1,1,2,2,3,3,5,6,7,6] },
      { id: "restaurant", kw: 6, type: "normal",
        value_per_slot: [2,3,6,7,5,3,3,5,7,8] },
      { id: "jewellery", kw: 3, type: "normal",
        value_per_slot: [1,2,3,3,3,3,4,5,5,3] },
      { id: "clothing", kw: 2, type: "normal",
        value_per_slot: [2,2,3,3,3,4,5,5,4,3] },
    ],
  };

  // ------------------------------------------------------------------- //
  // Helpers                                                            //
  // ------------------------------------------------------------------- //
  function setStatus(state, text) {
    els.status.dataset.state = state;
    els.status.textContent = text || state;
  }

  function showInputError(msg) {
    if (!msg) {
      els.inputErr.hidden = true;
      els.inputErr.textContent = "";
    } else {
      els.inputErr.hidden = false;
      els.inputErr.textContent = msg;
    }
  }

  function parseInput() {
    const raw = els.input.value.trim();
    if (!raw) throw new Error("Input is empty — paste JSON or click 'Load sample'.");
    let data;
    try {
      data = JSON.parse(raw);
    } catch (e) {
      throw new Error("Input is not valid JSON: " + e.message);
    }
    return data;
  }

  function fmt(n, digits) {
    if (n === null || n === undefined || Number.isNaN(n)) return "—";
    return Number(n).toFixed(digits == null ? 1 : digits);
  }

  function el(tag, props, children) {
    const e = document.createElement(tag);
    if (props) {
      for (const k in props) {
        if (k === "class") e.className = props[k];
        else if (k === "text") e.textContent = props[k];
        else if (k === "html") e.innerHTML = props[k];
        else if (k === "dataset") Object.assign(e.dataset, props[k]);
        else if (k.startsWith("on") && typeof props[k] === "function") {
          e.addEventListener(k.slice(2), props[k]);
        } else if (k in e) {
          e[k] = props[k];
        } else {
          e.setAttribute(k, props[k]);
        }
      }
    }
    (children || []).forEach((c) => {
      if (c == null) return;
      e.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return e;
  }

  // ------------------------------------------------------------------- //
  // Renderers                                                          //
  // ------------------------------------------------------------------- //
  function renderFuelChart(hourlyFuel, gridAvailable) {
    els.fuelChart.innerHTML = "";
    const max = Math.max(0.01, ...hourlyFuel);
    hourlyFuel.forEach((f, t) => {
      const isOutage = !gridAvailable[t];
      const heightPct = isOutage ? Math.max(2, (f / max) * 100) : 0;
      const bar = el("div", {
        class: "fuel-bar" + (f === 0 ? " zero" : ""),
        style: "height:" + heightPct + "%",
        title: "Hour " + t + (isOutage ? " (outage)" : " (grid)") + " — " + fmt(f, 2) + " L",
      });
      if (f > 0) {
        bar.appendChild(el("span", { class: "lbl", text: fmt(f, 1) }));
      }
      els.fuelChart.appendChild(bar);
    });
    // hour labels under bars
    const labelRow = el("div", {
      style:
        "display:flex; gap:0.4rem; padding:0 0.5rem; margin-top:0.2em;",
    });
    hourlyFuel.forEach((_, t) => {
      labelRow.appendChild(
        el("div", { class: "fuel-hour", text: String(t) }),
      );
    });
    els.fuelChart.appendChild(labelRow);
  }

  function renderRosterTable(payload, result) {
    const T = payload.grid_available.length;
    const shops = payload.shops;
    const gridAvail = payload.grid_available;
    const shopById = {};
    shops.forEach((s) => (shopById[s.id] = s));
    const stateById = {};
    result.shops.forEach((s) => (stateById[s.shop_id] = s));

    // Build table
    const tbl = els.rosterTbl;
    tbl.innerHTML = "";

    // header
    const thead = el("thead");
    const headRow = el("tr");
    headRow.appendChild(el("th", { class: "shop-col", text: "Shop" }));
    headRow.appendChild(el("th", { class: "shop-col", text: "kW" }));
    for (let t = 0; t < T; t++) {
      headRow.appendChild(
        el("th", { class: "hour-num", text: "h" + t }),
      );
    }
    headRow.appendChild(el("th", { class: "shop-col", text: "Value" }));
    thead.appendChild(headRow);
    tbl.appendChild(thead);

    // body
    const tbody = el("tbody");
    for (const shop of shops) {
      const row = el("tr");
      const stateRow = stateById[shop.id];
      const power = stateRow ? stateRow.power_state : [];
      const shopType = el("span", {
        class: "shop-type " + shop.type,
        text: shop.type,
      });
      const nameCell = el("td", { class: "shop-col" }, [
        el("span", { text: shop.id }),
        shopType,
      ]);
      row.appendChild(nameCell);
      row.appendChild(el("td", { class: "shop-col", text: String(shop.kw) }));
      for (let t = 0; t < T; t++) {
        const powered = power[t] === 1;
        const isGrid = gridAvail[t];
        let cls, label, title;
        if (isGrid) {
          cls = "cell-grid";
          label = "grid";
          title = "Hour " + t + ": grid available — shop is on automatically.";
        } else if (powered) {
          cls = "cell-on";
          label = "ON";
          title =
            "Hour " + t +
            ": generator on, shop powered (value " + shop.value_per_slot[t] + ").";
        } else {
          cls = "cell-off";
          if (shop.type === "mandatory") cls += " mandatory";
          label = "off";
          title = "Hour " + t + ": shop is dark. See reasons below.";
        }
        row.appendChild(
          el("td", { class: "hour-cell " + cls, text: label, title: title }),
        );
      }
      row.appendChild(
        el("td", { class: "row-total", text: fmt(stateRow ? stateRow.total_value_served : 0, 1) }),
      );
      tbody.appendChild(row);
    }
    tbl.appendChild(tbody);

    // footer row: grid availability
    const footRow = el("tr");
    footRow.appendChild(el("td", { class: "shop-col", text: "grid" }));
    footRow.appendChild(el("td", { class: "shop-col", text: "" }));
    for (let t = 0; t < T; t++) {
      footRow.appendChild(
        el("td", {
          class: "hour-cell " + (gridAvail[t] ? "cell-grid" : "cell-off"),
          text: gridAvail[t] ? "on" : "off",
        }),
      );
    }
    footRow.appendChild(el("td", { class: "shop-col", text: "" }));
    tbl.appendChild(footRow);
  }

  function renderDarkReasons(payload, result) {
    els.reasons.innerHTML = "";
    const shopById = {};
    payload.shops.forEach((s) => (shopById[s.id] = s));
    // sort: shops with reasons first, then alphabetical
    const sorted = result.shops.slice().sort((a, b) => {
      const ar = a.dark_reasons.length;
      const br = b.dark_reasons.length;
      if (ar !== br) return br - ar;
      return a.shop_id.localeCompare(b.shop_id);
    });
    for (const s of sorted) {
      const card = el("div", {
        class: "reason-card" + (s.dark_reasons.length === 0 ? " empty" : ""),
      });
      const head = el("h4", { text: s.shop_id });
      head.appendChild(
        el("span", {
          class: "muted small",
          text: "  • value served " + fmt(s.total_value_served, 1),
        }),
      );
      card.appendChild(head);
      if (s.dark_reasons.length === 0) {
        card.appendChild(
          el("p", { class: "muted small", text: "Never dark. Powered every hour." }),
        );
      } else {
        const ul = el("ul");
        s.dark_reasons.forEach((r) => ul.appendChild(el("li", { text: r })));
        card.appendChild(ul);
      }
      els.reasons.appendChild(card);
    }
  }

  // ------------------------------------------------------------------- //
  // Main render                                                        //
  // ------------------------------------------------------------------- //
  function renderResult(payload, result) {
    els.results.hidden = false;

    // status pill
    setStatus(result.status, result.status);
    els.kpiStatus.textContent = result.status;
    els.kpiValue.textContent  = fmt(result.total_value_served, 1);
    els.kpiFuel.textContent   = fmt(result.total_fuel_used_litres, 2) + " L";

    // reveal the print button now that there is a roster to print
    if (els.btnPrint) els.btnPrint.hidden = false;

    // infeasible banner
    if (result.status === "infeasible" || (result.infeasibility_reasons && result.infeasibility_reasons.length > 0)) {
      els.infeas.hidden = false;
      els.infeasList.innerHTML = "";
      (result.infeasibility_reasons || []).forEach((r) =>
        els.infeasList.appendChild(el("li", { text: r })),
      );
    } else {
      els.infeas.hidden = true;
    }

    renderFuelChart(result.hourly_fuel_litres, payload.grid_available);
    renderRosterTable(payload, result);
    renderDarkReasons(payload, result);

    // scroll into view
    els.results.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ------------------------------------------------------------------- //
  // Wire-up                                                            //
  // ------------------------------------------------------------------- //
  els.btnSample.addEventListener("click", function () {
    els.input.value = JSON.stringify(SAMPLE, null, 2);
    showInputError(null);
  });

  els.btnSolve.addEventListener("click", async function () {
    showInputError(null);
    let payload;
    try {
      payload = parseInput();
    } catch (e) {
      showInputError(e.message);
      setStatus("error", "bad input");
      return;
    }

    setStatus("solving", "solving…");
    els.elapsed.textContent = "";
    els.btnSolve.disabled = true;
    const t0 = performance.now();

    try {
      const resp = await fetch("/api/roster", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (resp.status === 400) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.detail || "Bad request (HTTP 400).");
      }
      if (!resp.ok) {
        const text = await resp.text();
        throw new Error("HTTP " + resp.status + ": " + text);
      }
      const result = await resp.json();
      const elapsed = ((performance.now() - t0) / 1000).toFixed(2);
      els.elapsed.textContent = "solved in " + elapsed + " s";
      renderResult(payload, result);
    } catch (e) {
      showInputError("Network or server error: " + e.message);
      setStatus("error", "error");
    } finally {
      els.btnSolve.disabled = false;
    }
  });

  // Print the rendered roster board to a single A4 page.
  // The @media print block in styles.css strips out everything except the
  // result card, KPIs, fuel chart, roster table, and dark-reason list.
  els.btnPrint.addEventListener("click", function () {
    window.print();
  });

  // pre-load sample so the page is interactive on first open
  els.input.value = JSON.stringify(SAMPLE, null, 2);
})();
