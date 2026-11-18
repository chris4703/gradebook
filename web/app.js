"use strict";

const $ = (id) => document.getElementById(id);
const labels = {
  missing: "Missing", past: "Past due", today: "Due today", upcoming: "Upcoming", review: "Review", all: "To do", tracked: "Tracked", record: "All assignments",
};
const steps = Object.fromEntries([...$("step").options].map(({ value, text }) => [value, text]));
const shortDate = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
const longDate = new Intl.DateTimeFormat("en-US", { weekday: "long", month: "long", day: "numeric", timeZone: "UTC" });
const assignmentColumns = ["course", "title", "due", "status", "points", "possible", "source_id", "score_percent"];
const formatNumber = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
let state;
let filter = "all";
let selectedId;
let showingWhatIf = false;
const trialScores = new Map();

function dateObject(iso) {
  return new Date(iso + "T00:00:00Z");
}

function element(tag, value, className) {
  const node = document.createElement(tag);
  if (value != null) node.textContent = value;
  if (className) node.className = className;
  return node;
}

async function api(path, payload) {
  const options = payload === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Gradebook": "1" },
    body: JSON.stringify(payload),
  };
  const response = await fetch("/api/" + path, options);
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(data.error || "Gradebook couldn’t complete the request.");
    error.status = response.status;
    throw error;
  }
  return data;
}

function showLogin() {
  state = undefined;
  trialScores.clear();
  showingWhatIf = false;
  $("whatif").hidden = true;
  $("whatif-assignments").replaceChildren();
  $("dashboard").hidden = true;
  $("content").hidden = true;
  $("top-actions").hidden = true;
  for (const id of ["work-list", "grade-list"]) $(id).replaceChildren();
  for (const id of ["key", "student", "term", "as-of", "grade-csv", "assignment-csv", "campus-student", "campus-grades", "campus-assignments", "course-breakdown", "assignment-detail", "note"]) $(id).value = "";
  for (const dialog of document.querySelectorAll("dialog[open]")) dialog.close();
  $("signin").hidden = false;
  $("key").focus();
}

async function refresh() {
  const latest = await api("dashboard");
  if (JSON.stringify(state?.models) !== JSON.stringify(latest.models) ||
      JSON.stringify(state?.assignments) !== JSON.stringify(latest.assignments) ||
      JSON.stringify(state?.grades) !== JSON.stringify(latest.grades)) trialScores.clear();
  state = latest;
  $("signin").hidden = true;
  $("top-actions").hidden = false;
  $("dashboard").hidden = false;
  $("import-button").hidden = state.role !== "parent";
  $("enrich-button").hidden = state.role !== "parent";
  $("date").textContent = longDate.format(dateObject(state.today));
  $("greeting").textContent = state.student ? "Hi, " + state.student + "." : "School dashboard";
  $("snapshot").textContent = state.asOf
    ? state.term + "  ·  Infinite Campus snapshot from " + shortDate.format(dateObject(state.asOf))
    : "Waiting for the first school snapshot";
  $("whatif-button").hidden = !state.student;
  $("dashboard-hero").hidden = showingWhatIf;
  $("content").hidden = !state.student || showingWhatIf;
  $("empty").hidden = Boolean(state.student);
  $("goal-track").replaceChildren();
  $("goal-summary").textContent = "";
  if (!state.student) return;

  const courses = [...new Set([...state.grades, ...state.assignments].map((row) => row.course))].sort();
  const previous = $("course").value;
  $("course").replaceChildren(new Option("All classes", ""), ...courses.map((course) => new Option(course, course)));
  if (courses.includes(previous)) $("course").value = previous;
  renderPlan();
  renderWork();
  if (showingWhatIf) renderWhatIf();
}

function renderPlan() {
  const bands = state.grades.map((row) => row.letter.charAt(0));
  const met = bands.filter((band) => band === "A" || band === "B").length;
  const lowest = [...bands].sort().at(-1);
  const unknown = bands.includes("");
  const track = bands.map((_, index) => element("span", "", index < met ? "met" : ""));
  $("goal-track").replaceChildren(...track);
  $("goal-summary").textContent = met + " of " + bands.length + " classes at goal";
  const trusted = met === bands.length;
  const oneHour = lowest === "D" || lowest === "F";
  const confirm = unknown && !oneHour;
  $("plan-caption").textContent = trusted ? "You’ve reached the grade goal." :
    confirm ? "Check the plan with a parent." : "Today’s plan · Lowest grade " + lowest;
  $("homework-time").textContent = trusted ? "Optional check-in" : "3:45–4:45 PM";
  $("homework-title").textContent = trusted ? "Homework" : "Homework with help";
  $("free-time").hidden = trusted;
  $("screen-label").textContent = trusted ? "Screen time" : "After dinner";
  $("screen-rule").textContent = trusted ? "You manage your screen time" :
    confirm ? "Check with a parent" : (oneHour ? "1 hour" : "3 hours") + " of PC or iPad";
  $("screen-detail").textContent = trusted ? "Your family trusts you to decide." :
    confirm ? "A grade needs a family decision." : "Daily total across both devices.";
  $("homework").setAttribute("aria-pressed", String(state.homework));
  $("homework").lastElementChild.textContent = state.homework ? "Homework checked in" : "Mark homework done";

  const gradeRows = state.grades.map((row) => {
    const line = element("div", null, "grade-row");
    const value = element("div", null, "grade-value");
    if (row.percent != null) value.append(element("span", row.percent + "%"));
    const letter = element("span", row.letter || "—", "letter");
    letter.dataset.band = row.letter.charAt(0);
    value.append(letter);
    line.append(element("span", row.course), value);
    return line;
  });
  $("grade-list").replaceChildren(...gradeRows);
}

function percentage(row) {
  return row.points == null ? row.score_percent ?? null : 100 * row.points / row.possible;
}

function bucket(row) {
  if (row.status === "missing") return "missing";
  if (["submitted", "excused"].includes(row.status)) return "done";
  const score = percentage(row);
  if (score != null && score < 80) return "review";
  if (row.status === "graded") return "done";
  if (row.due < state.today) return "past";
  return row.due === state.today ? "today" : "upcoming";
}

function recoverable(row) {
  if (row.possible == null) return null;
  if (row.status === "missing" && row.points == null) return row.possible;
  return row.points == null ? null : Math.max(0, row.possible - row.points);
}

function estimate(row) {
  return state.estimates?.[row.course]?.[row.id];
}

function pointsLabel(amount) {
  return formatNumber.format(amount) + (amount === 1 ? " point" : " points");
}

function renderRecovery(rows) {
  const byGrade = $("recovery-sort").value === "impact";
  const impact = (row) => estimate(row)?.gain ?? -1;
  const grade = new Map(state.grades.map((row) => [row.course, row.percent ?? 101]));
  const candidates = rows.filter((row) => ["missing", "graded"].includes(row.status) &&
    recoverable(row) > 0 && estimate(row)?.gain !== 0 && !["resolved", "submitted"].includes(state.progress[row.id]?.step));
  candidates.sort((a, b) => (byGrade ? impact(b) - impact(a) : 0) ||
    recoverable(b) - recoverable(a) || (grade.get(a.course) ?? 101) - (grade.get(b.course) ?? 101) ||
    a.due.localeCompare(b.due));
  const withEstimate = candidates.some((row) => estimate(row));
  $("recovery-caption").textContent = byGrade && withEstimate ?
    "Sorted by estimated class grade gain where Campus provided a matching breakdown." :
    "Sorted by points still available. Point values can affect each class grade differently.";
  for (const status of ["missing", "graded"]) {
    const items = candidates.filter((row) => row.status === status).slice(0, 3);
    const nodes = items.map((row) => {
      const line = element("article", null, "recovery-row");
      const title = element("button", row.title, "recovery-title");
      title.addEventListener("click", () => openProgress(row, status === "missing" ? "Missing" : "Scored"));
      const info = estimate(row);
      const gain = info ? " · +" + formatNumber.format(info.gain) + " percentage points" : "";
      line.append(title, element("p", row.course + (info ? " · " + info.category : "")),
        element("strong", pointsLabel(recoverable(row)) + " available" + gain));
      return line;
    });
    const id = status === "missing" ? "recover-missing" : "recover-graded";
    $(id).replaceChildren(...(nodes.length ? nodes : [element("p", "No scored point opportunities in this view.", "recovery-empty")]));
  }
}

function renderWork() {
  const className = $("course").value;
  const rows = state.assignments.filter((row) => !className || row.course === className)
    .map((row) => ({ ...row, bucket: bucket(row) }));
  renderRecovery(rows);
  const order = ["missing", "today", "past", "upcoming", "review", "done"];
  rows.sort((left, right) => order.indexOf(left.bucket) - order.indexOf(right.bucket) ||
    (left.bucket === "review" || left.bucket === "done" ? right.due.localeCompare(left.due) : left.due.localeCompare(right.due)));
  const tracked = rows.filter((row) => {
    const progress = state.progress[row.id];
    return progress?.step || progress?.note;
  });
  const work = rows.filter((row) => row.bucket !== "done" && state.progress[row.id]?.step !== "resolved");
  const makeTab = (key) => {
    const count = key === "record" ? rows.length : key === "tracked" ? tracked.length : key === "all" ? work.length : work.filter((row) => row.bucket === key).length;
    const button = element("button", labels[key], "filter");
    button.dataset.filter = key;
    button.append(element("span", count));
    button.setAttribute("aria-pressed", String(filter === key));
    button.addEventListener("click", () => {
      filter = key;
      renderWork();
      $("tabs").querySelector('[data-filter="' + key + '"]').focus();
    });
    return button;
  };
  $("tabs").replaceChildren(...["all", "missing", "past", "today", "upcoming", "review", "tracked", "record"].map(makeTab));
  $("work-caption").textContent = work.length ? work.length + " items to plan or follow up on" : "You’re caught up in this view.";
  const shown = filter === "record" ? rows : filter === "tracked" ? tracked : work.filter((row) => filter === "all" || row.bucket === filter);
  $("work-list").replaceChildren(...(shown.length ? shown.map(assignment) :
    [element("p", "Nothing to follow up on in this view.", "empty")]));
}

function assignment(row) {
  const line = element("article", null, "assignment");
  const copy = element("div");
  const meta = [row.course, "Due " + shortDate.format(dateObject(row.due))];
  const score = percentage(row);
  if (row.points != null) meta.push(formatNumber.format(row.points) + "/" + formatNumber.format(row.possible) + " pts");
  else meta.push(row.possible == null ? "Points not listed" : pointsLabel(row.possible) + " possible");
  if (score != null) meta.push(formatNumber.format(score) + "%");
  if (row.bucket === "past" && row.possible != null) meta.push("Check whether it was submitted");
  const progress = state.progress[row.id];
  copy.append(element("h3", row.title), element("p", meta.join("  ·  ")));
  if (progress?.step) copy.append(element("p", steps[progress.step]));
  if (progress?.note) copy.append(element("p", progress.note));
  const actions = element("div", null, "row-action");
  const tomorrow = (dateObject(row.due) - dateObject(state.today)) === 86400000;
  const tag = row.bucket === "past" ? "Check status" :
    row.bucket === "upcoming" && tomorrow ? "Due tomorrow" : labels[row.bucket] ||
    ({ graded: "Scored", submitted: "Submitted", excused: "Excused" })[row.status] || "Closed in Campus";
  actions.append(element("span", tag, "tag " + row.bucket));
  const button = element("button", progress?.step || progress?.note ? "Edit next step" : "Next step");
  button.setAttribute("aria-label", "Next step for " + row.title);
  button.addEventListener("click", () => openProgress(row, tag));
  actions.append(button);
  line.append(copy, actions);
  return line;
}

function openProgress(row, label) {
  const progress = state.progress[row.id];
  selectedId = row.id;
  $("progress-title").textContent = row.title;
  $("progress-meta").textContent = row.course + " · " + label;
  $("step").value = progress?.step || "";
  $("note").value = progress?.note || "";
  $("progress-dialog").querySelector(".error").textContent = "";
  $("progress-dialog").showModal();
}

function classPercent(groups) {
  const total = (field) => groups.reduce((sum, group) => sum + group[field], 0);
  if (groups[0].weight === null) return 100 * total("earned") / total("possible");
  return 100 * groups.reduce((sum, group) => sum + group.weight * group.earned / group.possible, 0) / total("weight");
}

function estimatedLetter(percent) {
  const rounded = Math.round(percent);
  if (rounded >= 90) return "A";
  if (rounded >= 80) return "B";
  if (rounded >= 70) return "C";
  if (rounded >= 60) return "D";
  return "F";
}

function displayWhatIfGrade(id, percent, letter) {
  const field = $(id);
  field.textContent = percent == null ? "—" : formatNumber.format(percent) + "%";
  if (letter) field.append(" ", element("span", letter, "whatif-letter"));
}

function renderProjection(course, entries) {
  const grade = state.grades.find((row) => row.course === course);
  const actual = grade?.percent;
  const model = state.models?.[course];
  displayWhatIfGrade("whatif-actual", actual, grade?.letter);
  displayWhatIfGrade("whatif-projected", null, null);
  $("whatif-change").textContent = "—";
  if (!model || actual == null) return;
  const groups = model.groups.map((group) => ({ ...group }));
  for (const row of entries) {
    if (!trialScores.has(row.id)) continue;
    const group = groups[model.assignments[row.id]];
    const points = trialScores.get(row.id);
    if (row.points == null) {
      group.earned += points;
      group.possible += row.possible;
    } else {
      group.earned += points - row.points;
    }
  }
  const change = Math.round((classPercent(groups) - classPercent(model.groups)) * 100) / 100;
  const projected = actual + change;
  displayWhatIfGrade("whatif-projected", projected, change === 0 ? grade.letter : estimatedLetter(projected));
  $("whatif-change").textContent = (change > 0 ? "+" : "") + formatNumber.format(change) + " pp";
}

function renderWhatIf() {
  const select = $("whatif-course");
  const previous = select.value;
  select.replaceChildren(...state.grades.map((row) => new Option(row.course, row.course)));
  if (state.grades.some((row) => row.course === previous)) select.value = previous;
  const course = select.value;
  const model = state.models?.[course];
  const entries = state.assignments.filter((row) => row.course === course && model?.assignments[row.id] != null &&
    row.possible != null && row.status !== "excused");
  entries.sort((a, b) => Number(b.status === "missing") - Number(a.status === "missing") || b.due.localeCompare(a.due));
  $("whatif-note").textContent = !model
    ? "A matching Campus class breakdown is needed before this class can be simulated."
    : entries.length
      ? "Change any scores below. Entering a score for ungraded work adds its points to the grade calculation."
      : "No assignments with points in this class are available to simulate.";
  $("whatif-assignments").replaceChildren(...entries.map((row) => {
    const field = element("div", null, "whatif-row");
    const label = element("label", row.title);
    label.htmlFor = "trial-" + row.id;
    const score = row.points == null ? "Ungraded" : formatNumber.format(row.points) + " earned";
    const detail = element("p", score + " · " + pointsLabel(row.possible) + " possible");
    const scoreField = element("div", null, "whatif-score");
    const input = element("input");
    input.type = "number";
    input.id = label.htmlFor;
    input.min = "0";
    input.max = String(Math.max(row.possible, row.points ?? 0));
    input.step = "any";
    input.inputMode = "decimal";
    input.placeholder = row.points ?? "";
    input.value = trialScores.get(row.id) ?? "";
    const range = element("span", "/ " + formatNumber.format(row.possible));
    input.addEventListener("input", () => {
      if (input.value === "" || !input.validity.valid) trialScores.delete(row.id);
      else trialScores.set(row.id, input.valueAsNumber);
      renderProjection(course, entries);
    });
    scoreField.append(input, range);
    const title = element("div");
    title.append(label, detail);
    field.append(title, scoreField);
    return field;
  }));
  renderProjection(course, entries);
}

function toggleWhatIf(show) {
  showingWhatIf = show;
  $("whatif").hidden = !show;
  $("content").hidden = show;
  $("dashboard-hero").hidden = show;
  $("whatif-button").setAttribute("aria-expanded", String(show));
  if (show) renderWhatIf();
}

$("whatif-button").addEventListener("click", () => toggleWhatIf(!showingWhatIf));
$("grades-link").addEventListener("click", () => {
  if (showingWhatIf) toggleWhatIf(false);
});
$("whatif-course").addEventListener("change", renderWhatIf);
$("whatif-reset").addEventListener("click", () => {
  trialScores.clear();
  renderWhatIf();
});

function csv(columns, rows = []) {
  const quote = (value) => '"' + String(value ?? "").replaceAll('"', '""') + '"';
  return [columns.join(","), ...rows.map((row) => columns.map((key) => quote(row[key])).join(","))].join("\n");
}

function form(id, action) {
  const node = $(id);
  const error = node.querySelector(".error");
  const button = node.querySelector("button.primary");
  node.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.textContent = "";
    button.disabled = true;
    try {
      await action();
    } catch (failure) {
      if (failure.status === 401 && id !== "login-form") showLogin();
      else error.textContent = failure.message;
    } finally {
      button.disabled = false;
    }
  });
}

async function run(button, action) {
  button.disabled = true;
  $("status").hidden = true;
  try {
    await action();
  } catch (error) {
    if (error.status === 401) showLogin();
    else {
      $("status").textContent = error.message;
      $("status").hidden = false;
    }
  } finally {
    button.disabled = false;
  }
}

form("login-form", async () => {
  await api("login", { key: $("key").value });
  $("key").value = "";
  await refresh();
});
form("progress-form", async () => {
  await api("progress", { id: selectedId, step: $("step").value, note: $("note").value });
  $("progress-dialog").close();
  await refresh();
});
form("import-form", async () => {
  const hadModels = Object.keys(state.models || {}).length > 0;
  await api("import", {
    student: $("student").value, term: $("term").value, asOf: $("as-of").value,
    grades: $("grade-csv").value, assignments: $("assignment-csv").value,
  });
  $("import-dialog").close();
  await refresh();
  const needsBreakdown = state.grades.filter((row) => !state.models?.[row.course]).map((row) => row.course);
  $("status").textContent = "School snapshot updated." + (hadModels
    ? needsBreakdown.length
      ? " Class breakdown needed for " + needsBreakdown.join(", ") + "."
      : " Class weights and totals updated."
    : "");
  $("status").hidden = false;
});
function openImport(snapshot, fromCampus) {
  $("student").value = snapshot.student || "";
  $("term").value = snapshot.term || "";
  $("as-of").value = fromCampus ? snapshot.asOf : state.today;
  $("as-of").max = state.today;
  $("grade-csv").value = csv(["course", "letter", "percent"], snapshot.grades);
  $("assignment-csv").value = csv(assignmentColumns, snapshot.assignments);
  $("csv-fields").open = !fromCampus;
  const summary = $("import-summary");
  summary.replaceChildren();
  if (fromCampus) {
    const missing = snapshot.assignments.filter((row) => row.status === "missing").length;
    summary.append(
      element("strong", snapshot.grades.length + " class grades and " + snapshot.assignments.length +
        " assignments (" + missing + " marked missing)."),
      element("br"),
      document.createTextNode(snapshot.grades.map((row) => row.course + " " + (row.letter || "—")).join(" · ")),
    );
  } else {
    summary.textContent = "Edit the school records below or paste CSV with the column headers shown.";
  }
  $("import-dialog").querySelector(".error").textContent = "";
  $("import-dialog").showModal();
}
form("campus-form", async () => {
  const snapshot = await api("campus-preview", {
    student: $("campus-student").value,
    grades: $("campus-grades").value,
    assignments: $("campus-assignments").value,
  });
  $("campus-dialog").close();
  openImport(snapshot, true);
});
$("campus-dialog").addEventListener("close", () => {
  $("campus-grades").value = "";
  $("campus-assignments").value = "";
});
$("import-button").addEventListener("click", () => {
  $("campus-student").value = state.student || "";
  $("campus-dialog").querySelector(".error").textContent = "";
  $("campus-dialog").showModal();
});
$("manual-import").addEventListener("click", () => {
  $("campus-dialog").close();
  openImport(state, false);
});
$("logout").addEventListener("click", () => run($("logout"), async () => {
  await api("logout", {});
  showLogin();
}));
$("homework").addEventListener("click", () => run($("homework"), async () => {
  await api("routine", { day: state.today, homework: !state.homework });
  await refresh();
}));
$("enrich-button").addEventListener("click", () => {
  $("grading-course").replaceChildren(...state.grades.map((row) => new Option(row.course + (state.models?.[row.course] ? "" : " (needs breakdown)"), row.course)));
  const eligible = state.assignments.filter((row) => row.points == null);
  $("points-form").hidden = !eligible.length;
  $("points-assignment").replaceChildren(...eligible.map((row) =>
    new Option(row.course + " · " + row.title, row.id)));
  for (const id of ["weights-form", "points-form"]) $(id).querySelector(".error").textContent = "";
  $("enrich-result").textContent = "";
  $("enrich-dialog").showModal();
});
form("weights-form", async () => {
  const result = await api("course-breakdown", { course: $("grading-course").value, text: $("course-breakdown").value });
  $("course-breakdown").value = "";
  $("enrich-result").textContent = "Class breakdown saved. " + result.count + " matching assignment estimates.";
  await refresh();
});
form("points-form", async () => {
  await api("assignment-points", { id: $("points-assignment").value, text: $("assignment-detail").value });
  $("assignment-detail").value = "";
  $("enrich-result").textContent = "Assignment points saved.";
  await refresh();
});
$("enrich-dialog").addEventListener("close", () => {
  $("course-breakdown").value = "";
  $("assignment-detail").value = "";
});
$("recovery-sort").addEventListener("change", renderWork);
$("course").addEventListener("change", renderWork);
for (const button of document.querySelectorAll("[data-close]")) {
  button.addEventListener("click", () => $(button.dataset.close).close());
}
refresh().catch((error) => {
  if (error.status === 401) showLogin();
  else $("loading").textContent = "Gradebook couldn’t load. Reload the page to try again.";
}).finally(() => {
  if ($("loading").textContent === "Opening Gradebook…") $("loading").hidden = true;
});
