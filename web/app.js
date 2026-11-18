"use strict";

const $ = (id) => document.getElementById(id);
const labels = {
  missing: "Missing", past: "Past due", today: "Due today", upcoming: "Upcoming", review: "Review", all: "All", tracked: "Tracked",
};
const steps = Object.fromEntries([...$("step").options].map(({ value, text }) => [value, text]));
const shortDate = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
const longDate = new Intl.DateTimeFormat("en-US", { weekday: "long", month: "long", day: "numeric", timeZone: "UTC" });
let state;
let filter = "all";
let selectedId;

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
  $("dashboard").hidden = true;
  $("content").hidden = true;
  $("top-actions").hidden = true;
  for (const id of ["work-list", "grade-list"]) $(id).replaceChildren();
  for (const id of ["key", "student", "term", "as-of", "grade-csv", "assignment-csv", "note"]) $(id).value = "";
  for (const dialog of document.querySelectorAll("dialog[open]")) dialog.close();
  $("signin").hidden = false;
  $("key").focus();
}

async function refresh() {
  state = await api("dashboard");
  $("signin").hidden = true;
  $("top-actions").hidden = false;
  $("dashboard").hidden = false;
  $("import-button").hidden = state.role !== "parent";
  $("date").textContent = longDate.format(dateObject(state.today));
  $("greeting").textContent = state.student ? "Hi, " + state.student + "." : "School dashboard";
  $("snapshot").textContent = state.asOf
    ? state.term + "  ·  Infinite Campus snapshot from " + shortDate.format(dateObject(state.asOf))
    : "Waiting for the first school snapshot";
  $("content").hidden = !state.student;
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
  const confirm = lowest === "F" || unknown;
  $("plan-caption").textContent = trusted ? "You’ve reached the grade goal." :
    confirm ? "Check the plan with a parent." : "Today’s plan · Lowest grade " + lowest;
  $("homework-time").textContent = trusted ? "Optional check-in" : "3:45–4:45 PM";
  $("homework-title").textContent = trusted ? "Homework" : "Homework with help";
  $("free-time").hidden = trusted;
  $("screen-label").textContent = trusted ? "Screen time" : "After dinner";
  $("screen-rule").textContent = trusted ? "You manage your screen time" :
    confirm ? "Check with a parent" : (lowest === "D" ? "1 hour" : "3 hours") + " of PC or iPad";
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
  return row.points == null ? null : 100 * row.points / row.possible;
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

function renderWork() {
  const className = $("course").value;
  const rows = state.assignments.filter((row) => !className || row.course === className)
    .map((row) => ({ ...row, bucket: bucket(row) }));
  const order = ["missing", "today", "past", "upcoming", "review", "done"];
  rows.sort((left, right) => order.indexOf(left.bucket) - order.indexOf(right.bucket) ||
    (left.bucket === "review" || left.bucket === "done" ? right.due.localeCompare(left.due) : left.due.localeCompare(right.due)));
  const tracked = rows.filter((row) => {
    const progress = state.progress[row.id];
    return progress?.step || progress?.note;
  });
  const work = rows.filter((row) => row.bucket !== "done" && state.progress[row.id]?.step !== "resolved");
  const makeTab = (key) => {
    const count = key === "tracked" ? tracked.length : key === "all" ? work.length : work.filter((row) => row.bucket === key).length;
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
  $("tabs").replaceChildren(...["all", "missing", "past", "today", "upcoming", "review", "tracked"].map(makeTab));
  $("work-caption").textContent = work.length ? work.length + " items to plan or follow up on" : "You’re caught up in this view.";
  const shown = filter === "tracked" ? tracked : work.filter((row) => filter === "all" || row.bucket === filter);
  $("work-list").replaceChildren(...(shown.length ? shown.map(assignment) :
    [element("p", "Nothing to follow up on in this view.", "empty")]));
}

function assignment(row) {
  const line = element("article", null, "assignment");
  const copy = element("div");
  const meta = [row.course, "Due " + shortDate.format(dateObject(row.due))];
  const score = percentage(row);
  if (score != null) meta.push(row.points + "/" + row.possible + " · " + Math.round(score) + "%");
  const progress = state.progress[row.id];
  copy.append(element("h3", row.title), element("p", meta.join("  ·  ")));
  if (progress?.step) copy.append(element("p", steps[progress.step]));
  if (progress?.note) copy.append(element("p", progress.note));
  const actions = element("div", null, "row-action");
  const tomorrow = (dateObject(row.due) - dateObject(state.today)) === 86400000;
  const tag = row.bucket === "past" ? "Check status" :
    row.bucket === "upcoming" && tomorrow ? "Due tomorrow" : labels[row.bucket] || "Closed in Campus";
  actions.append(element("span", tag, "tag " + row.bucket));
  const button = element("button", progress?.step || progress?.note ? "Edit next step" : "Next step");
  button.setAttribute("aria-label", "Next step for " + row.title);
  button.addEventListener("click", () => {
    selectedId = row.id;
    $("progress-title").textContent = row.title;
    $("progress-meta").textContent = row.course + " · " + tag;
    $("step").value = progress?.step || "";
    $("note").value = progress?.note || "";
    $("progress-dialog").querySelector(".error").textContent = "";
    $("progress-dialog").showModal();
  });
  actions.append(button);
  line.append(copy, actions);
  return line;
}

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
  await api("import", {
    student: $("student").value, term: $("term").value, asOf: $("as-of").value,
    grades: $("grade-csv").value, assignments: $("assignment-csv").value,
  });
  $("import-dialog").close();
  await refresh();
  $("status").textContent = "School snapshot updated.";
  $("status").hidden = false;
});
$("import-button").addEventListener("click", () => {
  $("student").value = state.student || "";
  $("term").value = state.term || "";
  $("as-of").value = state.asOf || state.today;
  $("as-of").max = state.today;
  $("grade-csv").value = csv(["course", "letter", "percent"], state.grades);
  $("assignment-csv").value = csv(["course", "title", "due", "status", "points", "possible", "source_id"], state.assignments);
  $("import-dialog").querySelector(".error").textContent = "";
  $("import-dialog").showModal();
});
$("logout").addEventListener("click", () => run($("logout"), async () => {
  await api("logout", {});
  showLogin();
}));
$("homework").addEventListener("click", () => run($("homework"), async () => {
  await api("routine", { day: state.today, homework: !state.homework });
  await refresh();
}));
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
