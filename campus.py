"""Read text a parent copies from the Campus Parent Grades and Assignments pages."""
import re
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
    points = possible = None
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
                points, possible = percent[1], "100"
            else:
                status = "graded" if status == "open" else status
        elif (not flag.startswith("COMMENTS") and flag not in OTHER_FLAGS
              and not POINTS.fullmatch(line) and not PERCENT.fullmatch(line)
              and (index == 0 or details[index - 1].upper() != "SCORE")):
            raise ValueError(f"Unrecognized assignment detail for {title}. Copy the full page or use CSV.")
    return dict(course=course, title=title, due=due.isoformat(), status=status,
                points=points, possible=possible, source_id="")


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
