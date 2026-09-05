/* Tap-to-roll dice (Phase 6). Pure client-side convenience roller — it only
 * does dice + arithmetic on values already recorded on the sheet
 * (Proficiency.bonus, the ability *_mod columns, Spell/Equipment
 * attack_bonus/damage_formula). It never validates enemy AC, decides hits
 * vs. misses, or tracks conditions/turn order — see CLAUDE.md.
 *
 * Loaded once from characters/sheet.html via <script src defer>. A single
 * delegated document click listener (not per-row bindings) is deliberate:
 * htmx swaps every row's outerHTML on edit/save/cancel/reorder/uses-adjust,
 * which would silently drop a listener bound directly to a row element.
 * Delegation on document survives every such swap, including rows added
 * after this script first runs, with no rebinding step needed anywhere.
 */
(function () {
  "use strict";

  var tray = document.getElementById("dice-tray");
  if (!tray) return; // only present on characters/sheet.html

  var labelEl = tray.querySelector(".dice-tray__label");
  var manualSection = tray.querySelector(".dice-tray__manual");
  var manualInput = tray.querySelector(".dice-tray__manual-input");
  var stage = tray.querySelector(".dice-tray__stage");
  var errorEl = tray.querySelector(".dice-tray__error");
  var resultEl = tray.querySelector(".dice-tray__result");
  var breakdownEl = tray.querySelector(".dice-tray__breakdown");
  var totalEl = tray.querySelector(".dice-tray__total");
  var mapSection = tray.querySelector(".dice-tray__map");
  var mapButtons = tray.querySelectorAll("[data-map]");
  var critBtn = tray.querySelector(".dice-tray__crit");

  var state = null; // the roll currently shown, or null when the tray is closed/empty

  function parseFormula(input) {
    var m = String(input).trim().match(/^(\d*)d(\d+)\s*([+-]\s*\d+)?$/i);
    if (!m) return null;
    var count = m[1] ? parseInt(m[1], 10) : 1;
    var sides = parseInt(m[2], 10);
    var modifier = m[3] ? parseInt(m[3].replace(/\s+/g, ""), 10) : 0;
    if (count < 1 || sides < 1) return null;
    return { count: count, sides: sides, modifier: modifier };
  }

  function rollOne(sides) {
    return Math.floor(Math.random() * sides) + 1;
  }

  function rollMany(count, sides) {
    var out = [];
    for (var i = 0; i < count; i++) out.push(rollOne(sides));
    return out;
  }

  function signed(n) {
    return n >= 0 ? "+ " + n : "− " + Math.abs(n);
  }

  function resetTray() {
    stage.innerHTML = "";
    errorEl.hidden = true;
    errorEl.textContent = "";
    resultEl.hidden = true;
    breakdownEl.textContent = "";
    totalEl.textContent = "";
    mapSection.hidden = true;
    critBtn.hidden = true;
    critBtn.setAttribute("aria-pressed", "false");
    manualSection.hidden = true;
    mapButtons.forEach(function (btn) {
      btn.setAttribute("aria-pressed", btn.getAttribute("data-map") === "0" ? "true" : "false");
    });
    state = null;
  }

  function openTray() {
    tray.hidden = false;
  }

  function closeTray() {
    tray.hidden = true;
    resetTray();
  }

  function showError(message) {
    stage.innerHTML = "";
    resultEl.hidden = true;
    mapSection.hidden = true;
    critBtn.hidden = true;
    errorEl.textContent = message;
    errorEl.hidden = false;
  }

  // Recomputes and redraws the breakdown/total from `state` — called once
  // when a roll settles, and again whenever a MAP or Crit button is toggled
  // (arithmetic only, never a re-roll: the die faces already landed).
  function renderResult() {
    var mapAmounts = state.agile ? [0, -4, -8] : [0, -5, -10];
    var mapAdjust = state.kind === "attack" ? mapAmounts[state.mapStep] : 0;
    var breakdown, base;

    if (state.isD20Roll) {
      var d20 = state.values[0];
      base = d20 + state.modifier + mapAdjust;
      breakdown = "d20 (" + d20 + ")";
      if (state.modifier) breakdown += " " + signed(state.modifier);
      if (mapAdjust) breakdown += " " + signed(mapAdjust) + " (MAP)";
    } else {
      var sum = state.values.reduce(function (a, b) { return a + b; }, 0);
      base = sum + state.modifier;
      breakdown = "[" + state.values.join(", ") + "]";
      if (state.modifier) breakdown += " " + signed(state.modifier);
    }

    var total = state.critDoubled ? base * 2 : base;
    breakdownEl.textContent = breakdown;
    totalEl.textContent = "Total: " + total + (state.critDoubled ? " (crit)" : "");
    resultEl.hidden = false;
  }

  // Renders one .die per value, staggered via an --i custom property the
  // CSS keyframe reads for animation-delay, then reveals the settled faces
  // (and a nat-20/nat-1 highlight on the d20, when this is a check/attack
  // roll) once the last die's flight animation ends.
  function animateDice(values, isD20Roll) {
    stage.innerHTML = "";
    errorEl.hidden = true;
    resultEl.hidden = true;

    var dieEls = values.map(function (value, i) {
      var die = document.createElement("span");
      die.className = "die";
      die.style.setProperty("--i", i);
      // Cosmetic-only jitter so identical dice (e.g. 2d6) don't fly in as
      // visual clones — never affects the rolled value.
      die.style.setProperty("--from-x", (Math.random() * 80 - 40).toFixed(0) + "px");
      die.style.setProperty("--from-y", (Math.random() * 60 - 70).toFixed(0) + "px");
      var face = document.createElement("span");
      face.className = "die__face";
      face.textContent = String(value);
      die.appendChild(face);
      stage.appendChild(die);
      return die;
    });

    var settled = false;
    var fallbackTimer = setTimeout(settle, 900); // in case animationend never fires

    function settle() {
      if (settled) return;
      settled = true;
      clearTimeout(fallbackTimer);
      dieEls.forEach(function (die, i) {
        die.classList.add("die--settled");
        if (isD20Roll && i === 0) {
          if (values[i] === 20) die.classList.add("die--crit-hit");
          if (values[i] === 1) die.classList.add("die--crit-miss");
        }
      });
      renderResult();
    }

    dieEls[dieEls.length - 1].addEventListener("animationend", settle, { once: true });
  }

  function handleTriggerClick(trigger) {
    var kind = trigger.getAttribute("data-roll");
    var label = trigger.getAttribute("data-roll-label") || "";

    resetTray();
    openTray();

    if (kind === "check" || kind === "attack") {
      var mod = parseInt(trigger.getAttribute("data-roll-mod"), 10);
      if (isNaN(mod)) mod = 0;
      state = {
        kind: kind,
        agile: trigger.hasAttribute("data-roll-agile"),
        isD20Roll: true,
        values: [rollOne(20)],
        modifier: mod,
        mapStep: 0,
        critDoubled: false,
      };
      labelEl.textContent = label + (kind === "attack" ? " — Attack" : "");
      mapSection.hidden = kind !== "attack";
      animateDice(state.values, true);
    } else if (kind === "damage") {
      var formula = parseFormula(trigger.getAttribute("data-roll-formula"));
      if (!formula) {
        labelEl.textContent = label + " — Damage";
        showError("Couldn't read that damage formula.");
        return;
      }
      state = {
        kind: "damage",
        agile: false,
        isD20Roll: false,
        values: rollMany(formula.count, formula.sides),
        modifier: formula.modifier,
        mapStep: 0,
        critDoubled: false,
      };
      labelEl.textContent = label + " — Damage";
      critBtn.hidden = false;
      animateDice(state.values, false);
    }
  }

  function handleManualOpen() {
    resetTray();
    openTray();
    manualSection.hidden = false;
    labelEl.textContent = "Manual roll";
    manualInput.value = "";
    manualInput.focus();
  }

  function handleManualRoll() {
    var formula = parseFormula(manualInput.value);
    if (!formula) {
      showError("Couldn't read that as dice notation, e.g. 2d6+4");
      return;
    }
    state = {
      kind: "manual",
      agile: false,
      isD20Roll: false,
      values: rollMany(formula.count, formula.sides),
      modifier: formula.modifier,
      mapStep: 0,
      critDoubled: false,
    };
    animateDice(state.values, false);
  }

  function handleMapClick(btn) {
    if (!state || state.kind !== "attack") return;
    state.mapStep = parseInt(btn.getAttribute("data-map"), 10);
    mapButtons.forEach(function (b) {
      b.setAttribute("aria-pressed", b === btn ? "true" : "false");
    });
    renderResult();
  }

  function handleCritClick() {
    if (!state || state.kind !== "damage") return;
    state.critDoubled = !state.critDoubled;
    critBtn.setAttribute("aria-pressed", state.critDoubled ? "true" : "false");
    renderResult();
  }

  document.addEventListener("click", function (e) {
    var trigger = e.target.closest("[data-roll]");
    if (trigger) {
      handleTriggerClick(trigger);
      return;
    }
    if (e.target.closest("[data-dice-manual-open]")) {
      handleManualOpen();
      return;
    }
    if (e.target.closest("[data-dice-close]")) {
      closeTray();
      return;
    }
    if (e.target.closest("[data-dice-manual-roll]")) {
      handleManualRoll();
      return;
    }
    var mapBtn = e.target.closest("[data-map]");
    if (mapBtn) {
      handleMapClick(mapBtn);
      return;
    }
    if (e.target.closest("[data-dice-crit]")) {
      handleCritClick();
    }
  });

  manualInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter") handleManualRoll();
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !tray.hidden) closeTray();
  });
})();
