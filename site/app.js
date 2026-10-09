"use strict";

const $ = (s) => document.querySelector(s);
const VERDICT = { incident: "Incident", broad: "Broad change (no single cause)", no_change: "No real change", cant_tell: "Can't tell" };
const OUTCOME = { right: "Right", wrong: "Wrong cause", false_alarm: "False alarm", missed: "Missed", abstained: "Can't tell" };
const KIND = { planted: "Planted incident", natural: "Real tracking failure", broad: "Holiday week", quiet: "Quiet week" };
let data = null;

function pct(x) { return x == null ? "n/a" : `${x >= 0 ? "+" : ""}${Math.round(x * 100)}%`; }

function verdictLine(a) {
  if (!a || !a.verdict) return "no answer";
  if (a.verdict !== "incident") return VERDICT[a.verdict] || a.verdict;
  return `${a.type || "Incident"} in ${a.segment}${a.start ? `, from ${a.start}` : ""}`;
}

function card(c) {
  const el = $("#card").content.firstElementChild.cloneNode(true);
  el.querySelector(".cid").textContent = c.id;
  const kind = el.querySelector(".kind");
  kind.textContent = KIND[c.kind] + (c.label === "too_small" ? " (too small to see)" : "") +
    (c.planted && ["landing_break", "units_bug"].includes(c.planted.type) ? " (type unseen in development)" : "");
  const o = el.querySelector(".outcome");
  o.textContent = OUTCOME[c.shipped.outcome] || "not run";
  o.classList.add(c.shipped.outcome || "");
  el.querySelector(".q").textContent = `What changed in ${c.metric.toLowerCase()} in the week of ${c.week}, and why?`;
  el.querySelector(".headline-change").textContent =
    `${c.metric}: ${pct(c.headline_change)} week over week, ${c.headline_real ? "beyond" : "within"} normal variation.`;
  el.querySelector(".verdict").textContent = verdictLine(c.shipped);
  const t = c.shipped.text || {};
  el.querySelector(".ahead").textContent = t.headline || "";
  const ul = el.querySelector(".expl");
  (t.explanation || []).forEach((s) => { const li = document.createElement("li"); li.textContent = s; ul.append(li); });
  el.querySelector(".next").textContent = t.next_step ? `Next step: ${t.next_step}` : "";
  el.querySelector(".notes").textContent = (c.shipped.notes || []).length ? `Code checks: ${c.shipped.notes.join("; ")}` : "";
  const p = el.querySelector(".planted");
  if (c.planted) {
    p.innerHTML = "";
    p.append(Object.assign(document.createElement("strong"), { textContent: "Planted: " }),
      `${c.planted.what} (${c.planted.segment}, from ${c.planted.start}). Hidden from the agent.`);
  } else {
    p.textContent = c.note || "";
  }
  const exp = c.expected;
  el.querySelector(".expect").textContent = exp.verdict === "incident"
    ? `Right answer: ${exp.type} in ${exp.segment}, from ${exp.start}` + (c.label === "too_small" ? " (or no real change / can't tell: too small to see)" : "")
    : `Right answer: ${(exp.accept && exp.accept.length ? exp.accept : [exp.verdict]).map((v) => VERDICT[v]).join(" or ")}`;
  el.querySelector(".base").textContent = `Rules alone (no model): ${verdictLine(c.baseline)} (${OUTCOME[c.baseline.outcome] || "n/a"})`;
  el.querySelector("pre").textContent = c.evidence;
  return el;
}

function render() {
  const split = $("#split").value, outcome = $("#outcome").value, kind = $("#kind").value;
  const rows = data.cases.filter((c) => (split === "all" || c.split === split) &&
    (outcome === "all" || c.shipped.outcome === outcome) && (kind === "all" || c.kind === kind));
  $("#count").textContent = `${rows.length} case${rows.length === 1 ? "" : "s"}`;
  const list = $("#list");
  list.replaceChildren(...rows.map(card));
}

function scores() {
  const s = data.summary.holdout.checked, d = data.summary.dev.checked;
  const box = $("#scores");
  const items = [
    [`${s.right}/${s.cases}`, "held-out cases right", false],
    [`${Math.round(s.wrong_rate * 100)}%`, "wrong causes (gate: 5% or less)", s.wrong_rate > 0.05],
    [`${Math.round((s.false_alarm_rate || 0) * 100)}%`, "false alarms on quiet weeks (gate: 10%)", (s.false_alarm_rate || 0) > 0.1],
    [`${d.right}/${d.cases}`, "development cases right", false],
    ["100%", "of numbers shown come from the data (dates the model typed itself were removed)", false],
  ];
  box.replaceChildren(...items.map(([b, span, fail]) => {
    const el = document.createElement("div");
    el.className = "score" + (fail ? " fail" : "");
    el.append(Object.assign(document.createElement("b"), { textContent: b }), Object.assign(document.createElement("span"), { textContent: span }));
    return el;
  }));
}

async function main() {
  data = await (await fetch("data/cases.json", { cache: "no-cache" })).json();
  scores();
  render();
  for (const id of ["#split", "#outcome", "#kind"]) $(id).addEventListener("change", render);
}
main();
