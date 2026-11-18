"""Read text a parent copies from the Campus Parent Grades and Assignments pages."""
import re
from copy import deepcopy
from datetime import date

DATE = r"(\d{1,2})/(\d{1,2})/(\d{4})"
TERM = re.compile(rf"Term\s+(.+?)\s*\(\s*{DATE}\s*[-–]\s*{DATE}\s*\)")
DAY = re.compile(rf"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+{DATE}")
POINTS = re.compile(r"(-?\d+(?:\.\d+)?)/\s*(\d+(?:\.\d+)?)")
PERCENT = re.compile(r"\(?(-?\d+(?:\.\d+)?)%\)?")
GRADE = re.compile(r"([A-D][+-]?|F)(?:\s+\(?(-?\d+(?:\.\d+)?)%\)?)?(?:\s+In-progress)?")
GRADE_LABEL = "Semester To Date"
FLAGS = {
    "MISSING": "missing", "TURNED IN": "submitted", "SUBMITTED": "submitted",
    "EXCUSED": "excused", "EXEMPT": "excused",
}
OTHER_FLAGS = {"LATE", "INCOMPLETE", "ABSENT", "CHEATED", "DROPPED",
               "NOT COUNTED", "NOT COUNTED IN GRADE"}


def lines(text):
    return [" ".join(line.split()) for line in text.splitlines() if line.strip()]


def campus_date(parts):
    month, day, year = map(int, parts)
    return date(year, month, day)


def grades_from_text(text):
    rows = lines(text)
    terms = [match for row in rows if (match := TERM.fullmatch(row))]
    if "Grades" not in rows or len(terms) != 1:
        raise ValueError("Copy the Grades page with only the current term selected.")
    term = terms[0]
    start = campus_date(term.groups()[1:4])
    end = campus_date(term.groups()[4:])
    if end < start:
        raise ValueError("Check the dates on the copied Grades page.")
    grades = []
    for index, row in enumerate(rows):
        if row != GRADE_LABEL or index == 0:
            continue
        course = rows[index - 1]
        values = rows[index + 1:index + 3]
        result = GRADE.fullmatch(values[0]) if values else None
        if not result:
            percent = PERCENT.fullmatch(values[0]) if values else None
            grades.append(dict(course=course, letter="", percent=percent[1] if percent else None))
            continue
        percent = PERCENT.fullmatch(values[1]) if len(values) > 1 else None
        grades.append(dict(course=course, letter=result[1], percent=result[2] or (percent[1] if percent else None)))
    if not grades:
        raise ValueError("No Semester To Date rows were found. Open Grades, expand all classes in Settings, then copy again.")
    return term[1].strip(), start, end, grades


def assignment_from_lines(rows, due):
    if len(rows) < 2:
        raise ValueError("An assignment is missing its title or class. Copy the whole Assignments page.")
    title, course, *details = rows
    status = "open"
    points = possible = score_percent = None
    for index, line in enumerate(details):
        flag = line.upper()
        if flag in FLAGS:
            candidate = FLAGS[flag]
            if status == "open" or candidate == "excused" or (status == "submitted" and candidate == "missing"):
                status = candidate
        elif flag == "SCORE":
            if index + 1 >= len(details):
                raise ValueError(f"The copied score for {title} is incomplete.")
            score = POINTS.fullmatch(details[index + 1])
            if score and float(score[2]) > 0:
                points, possible = score.groups()
                continue
            percent = PERCENT.fullmatch(details[index + 1])
            if percent:
                score_percent = percent[1]
            else:
                status = "graded" if status == "open" else status
        elif (not flag.startswith("COMMENTS") and flag not in OTHER_FLAGS
              and not POINTS.fullmatch(line) and not PERCENT.fullmatch(line)
              and (index == 0 or details[index - 1].upper() != "SCORE")):
            raise ValueError(f"Unrecognized assignment detail for {title}. Copy the full page or use CSV.")
    return dict(course=course, title=title, due=due.isoformat(), status=status,
                points=points, possible=possible, score_percent=score_percent, source_id="")


def assignments_from_text(text, start, end):
    rows = lines(text)
    if "Assignments" not in rows:
        raise ValueError("Copy the Assignments page with Current Term selected.")
    assignments = []
    due = None
    pending = None
    for row in rows:
        day = DAY.fullmatch(row)
        if day or row == "Assignment":
            if pending is not None:
                assignments.append(assignment_from_lines(pending, due))
                pending = None
            if day:
                due = campus_date(day.groups())
            elif due is None or not start <= due <= end:
                raise ValueError("The assignment dates do not match the grade term. Select Current Term in Assignments and copy again.")
            else:
                pending = []
        elif pending is not None and row not in {"TODAY", "No assignments."}:
            pending.append(row)
    if pending is not None:
        assignments.append(assignment_from_lines(pending, due))
    if not assignments and "No assignments." not in rows:
        raise ValueError("No assignment rows were found. Copy the whole Assignments page.")
    return assignments


def read_campus(grades, assignments):
    term, start, end, grade_rows = grades_from_text(grades)
    return term, grade_rows, assignments_from_text(assignments, start, end)


def course_model(text, snapshot, course, as_of):
    if snapshot.get("asOf") != as_of:
        raise ValueError("Update the Campus snapshot before adding the current class breakdown.")
    grade = next((row for row in snapshot["grades"] if row["course"] == course), None)
    rows = lines(text)
    term = [match[1] for row in rows if (match := TERM.fullmatch(row))]
    heading = f"({snapshot['term']}) {GRADE_LABEL}"
    if not grade or grade["percent"] is None or rows[:1] != [course] or term != [snapshot["term"]] or heading not in rows:
        raise ValueError("Copy the selected class’s Semester To Date breakdown for the snapshot term.")
    start = rows.index(heading) + 1
    end = next((i for i in range(start, len(rows)) if rows[i].startswith(f"({snapshot['term']}) ")), len(rows))
    rows = rows[start:end]
    categories = []
    for i, row in enumerate(rows):
        match = POINTS.fullmatch(row)
        if not match or i < 1 or i+1 >= len(rows) or rows[i-1] in {"Score", "Grade Total"}:
            continue
        percent = PERCENT.fullmatch(rows[i+1])
        if not percent:
            continue
        weight = re.fullmatch(r"Weight:\s*(\d+(?:\.\d+)?)", rows[i-1])
        name_index = i - (2 if weight else 1)
        if name_index < 0:
            continue
        earned, possible = map(float, match.groups())
        if not possible or abs(100*earned/possible - float(percent[1])) > .03:
            raise ValueError("Campus category percentages do not reconcile with their points.")
        categories.append(dict(index=name_index, name=rows[name_index], earned=earned, possible=possible,
                               weight=float(weight[1]) if weight else None))
    if not categories:
        raise ValueError("No category totals found. Expand every category and copy the class breakdown.")
    if all(cat["weight"] is not None for cat in categories):
        weight_total = sum(cat["weight"] for cat in categories)
        if not weight_total:
            raise ValueError("No positive grading weights were found.")
    elif all(cat["weight"] is None for cat in categories) and "Grade Total" in rows:
        total_index = rows.index("Grade Total") + 1
        total = POINTS.fullmatch(rows[total_index]) if total_index < len(rows) else None
        if not total or not float(total[2]) or any(abs(sum(cat[key] for cat in categories) - float(value)) > .02
                for key, value in zip(("earned", "possible"), total.groups())):
            raise ValueError("Category totals do not match the course’s point total.")
    else:
        raise ValueError("Campus did not provide a consistent grading formula for this course.")

    groups = [{key: v for key, v in cat.items() if key != "index"} for cat in categories]
    model = {"groups": groups, "assignments": {}}
    if not reconciles(model, grade["percent"]):
        raise ValueError("The class breakdown does not match the saved grade. Refresh the Campus snapshot and try again.")

    current = {(row["title"], row["due"]): row for row in snapshot["assignments"] if row["course"] == course}
    for index, (cat, following) in enumerate(zip(categories, [*categories[1:], {"index": len(rows)}])):
        category_rows = rows[cat["index"]:following["index"]]
        starts = [i for i, row in enumerate(category_rows) if row == "Assignment"]
        for a, b in zip(starts, [*starts[1:], len(category_rows)]):
            block = category_rows[a+1:b]
            if len(block) < 2 or any(re.match(r"^(?:DROPPED|EXCUSED|EXEMPT|NOT COUNTED|EXCLUDED)\b", part, re.I)
                                     for part in block[2:]):
                continue
            due = re.match(rf"Due:\s*{DATE}\b", block[1])
            score_index = next((i+1 for i, part in enumerate(block[:-1]) if part == "Score"), None)
            score = POINTS.fullmatch(block[score_index]) if score_index is not None else None
            if not due:
                continue
            row = current.get((block[0], campus_date(due.groups()).isoformat()))
            if not row or row["status"] == "excused":
                continue
            if score and (row["points"], row["possible"]) == tuple(map(float, score.groups())):
                model["assignments"][row["id"]] = index
            elif not score and row["points"] is None and row.get("score_percent") is None:
                model["assignments"][row["id"]] = index
    return model


def class_percent(groups):
    if any(group["possible"] <= 0 for group in groups):
        return None
    if groups[0]["weight"] is None:
        return 100 * sum(group["earned"] for group in groups) / sum(group["possible"] for group in groups)
    weight = sum(group["weight"] for group in groups)
    return 100 * sum(group["weight"] * group["earned"] / group["possible"] for group in groups) / weight


def reconciles(model, percent):
    calculated = class_percent(model["groups"])
    return percent is not None and calculated is not None and abs(calculated - percent) <= .03


def row_score(row):
    if row is None or row["status"] == "excused":
        return 0, 0
    if row.get("score_percent") is not None and row["points"] is None:
        return None
    return (row["points"], row["possible"]) if row["points"] is not None else (0, 0)


def replacement_estimates(model, rows):
    groups = model["groups"]
    total_possible = sum(group["possible"] for group in groups)
    total_weight = sum(group["weight"] or 0 for group in groups)
    estimates = {}
    for row in rows:
        group_index = model["assignments"].get(row["id"])
        if group_index is None or row["status"] not in {"missing", "graded"} or row["points"] is None:
            continue
        group = groups[group_index]
        recoverable = max(0, row["possible"] - row["points"])
        if total_weight:
            gain = 100 * group["weight"] / total_weight * recoverable / group["possible"]
        else:
            gain = 100 * recoverable / total_possible
        estimates[row["id"]] = {"category": group["name"], "gain": round(gain, 3)}
    return estimates


def rebase_models(old, new):
    previous = {row["id"]: row for row in old["assignments"]}
    current = {row["id"]: row for row in new["assignments"]}
    models, estimates = {}, {}
    for grade in new["grades"]:
        course = grade["course"]
        saved = old.get("models", {}).get(course)
        if not saved:
            continue
        model = deepcopy(saved)
        ids = {key for records in (previous, current) for key, row in records.items() if row["course"] == course}
        supported = True
        for key in sorted(ids):
            before, after = previous.get(key), current.get(key)
            a, b = row_score(before), row_score(after)
            if a == b:
                continue
            index = model["assignments"].get(key)
            if index is None or a is None or b is None:
                supported = False
                break
            group = model["groups"][index]
            group["earned"] += b[0] - a[0]
            group["possible"] += b[1] - a[1]
        if not supported or not reconciles(model, grade["percent"]):
            continue
        model["assignments"] = {key: index for key, index in model["assignments"].items() if key in current}
        models[course] = model
        estimates[course] = replacement_estimates(model, [current[key] for key in sorted(ids & current.keys())])
    return models, estimates


def detail_points(text, assignment):
    rows = lines(text)
    if assignment["title"] not in rows or assignment["course"] not in rows:
        raise ValueError("Copy the details page for the selected assignment.")
    ends = [match for row in rows if (match := re.fullmatch(rf"End Date\s*:\s*{DATE}(?:\s.*)?", row))]
    for i, row in enumerate(rows[:-1]):
        if row.rstrip(": ").strip() == "End Date":
            match = re.match(rf"{DATE}\b", rows[i+1])
            if match:
                ends.append(match)
    if ends and not any(campus_date(match.groups()).isoformat() == assignment["due"] for match in ends):
        raise ValueError("The copied assignment due date does not match the dashboard.")
    values = [rows[i+1] for i, row in enumerate(rows[:-1]) if row.rstrip(": ").strip() == "Total Points"]
    if len(values) != 1 or not re.fullmatch(r"\d+(?:\.\d+)?", values[0]) or not 0 < float(values[0]) <= 10000:
        raise ValueError("Could not find a single Total Points value in the copied assignment details.")
    return float(values[0])
