# Gradebook

A family dashboard for middle-school work and an after-school plan. It runs as a single Docker Compose service and stores data in a local SQLite database. The app needs no external account, cloud database, or school-portal credentials.

## What the dashboard shows

- Missing assignments use Infinite Campus’s missing flag. An open item with an earlier due date appears under **Past due** with **Check status**. It can be awaiting a score.
- **Due today** and **Upcoming** use the Phoenix calendar date. Tomorrow’s items are labeled in the upcoming list.
- **Review** includes graded work below 80%. Use **Next step** to record reviewing mistakes, asking about a redo or retake, or working on a submission. **Tracked** keeps those items visible. **Finished follow-up** removes the item from the active queue. Assignment notes and steps stay in Gradebook.
- The goal is A or B in every graded class, using the school’s reported letter grades, including plus and minus variants. Below the goal, the school-day plan is homework with help from 3:45 to 4:45 PM, then screen-free time until dinner. Any D or F gives one hour of PC or iPad after dinner. With a lowest grade of C, it shows three hours. If a letter grade is unavailable and no D or F is reported, the dashboard calls for a parent decision. With all A’s and B’s, screen time is self-managed and the homework check-in is optional.
- The homework check-in resets at midnight in America/Phoenix. Gradebook displays the plan and records progress. Device limits are handled by the family.

## Run with Docker on the Mac Mini

Copy this directory or clone the repository on the Mac Mini. Run from the checkout:

```sh
cp .env.example .env
chmod 600 .env
```

Set separate random values for `GRADEBOOK_FAMILY_KEY`, `GRADEBOOK_PARENT_KEY`, and `GRADEBOOK_SESSION_SECRET` in `.env`. Use at least 16 characters for each. For each value, you can generate a fresh key with `openssl rand -hex 24`. Share only the family key with the student. The parent key also enables school data updates. Keep the session secret on the server.

```sh
docker compose config --quiet
docker compose up -d --build --wait
curl -fsS http://127.0.0.1:8786/health
```

The default binds to loopback on port 8786. You can access that setup from another computer via an SSH tunnel:

```sh
ssh -N -L 8786:127.0.0.1:8786 macmini
```

Then open [Gradebook locally](http://127.0.0.1:8786). For direct access on a trusted home LAN, set `BIND_IP` to the Mac Mini’s LAN IPv4 address and open `http://MAC_MINI_LAN_IP:8786` from a family device. The direct LAN endpoint uses HTTP. Keep it on the home network. For access away from home, use a private VPN or an authenticated HTTPS reverse proxy and keep Gradebook’s access keys enabled. If the Mac Mini’s DHCP address changes, update `BIND_IP` and recreate the service. Docker Desktop must be running.

## Refresh Infinite Campus data

Gradebook shows the date of the last snapshot. The fastest refresh is to copy two Campus Parent pages and let Gradebook read the pasted text.

1. In Campus Parent, select the student and open **Grades**. Select only the current term and choose **Settings → Expand All** so the **Semester To Date** rows are visible.
2. In Gradebook, sign in with the parent key and choose **Update snapshot**. Click inside the content of the Campus Grades page, select all, copy, and paste in **Copy Grades**.
3. In Campus Parent, open **Assignments**, select **Current Term**, and clear the **Missing** filter. Click inside the page, select all, copy, and paste in **Copy Assignments**. Use the same student as on Grades.
4. Choose **Preview import**, check the class grades and assignment counts, then choose **Save snapshot**. The copy-and-paste import uses the date of the preview. Use the date field for a snapshot from an earlier day.

This reads only the text you paste into Gradebook. Campus Parent documents portal views, notifications, and downloadable school documents. Unattended updates would need a school-approved feed with assignments and in-progress grades, such as a district-provisioned OneRoster connection where the required data is available. See the [Campus Parent tools](https://kb.infinitecampus.com/help/tools-in-campus-parent) and [OneRoster prerequisites](https://kb.infinitecampus.com/help/oneroster-prerequisites).

### CSV fallback

Choose **Use CSV instead** or expand **Review or edit CSV** in the preview. The form starts with the relevant rows so you can edit them or paste CSV from a spreadsheet.

Grades CSV:

```csv
course,letter,percent
Example Math,C+,78.5
Example Science,B,86
```

Assignments CSV:

```csv
course,title,due,status,points,possible,source_id
Example Math,Practice set,2026-11-20,missing,0,10,
Example Math,Unit quiz,2026-11-16,graded,12,20,
Example Science,"Read chapter 5, part 2",2026-11-21,open,,,
```

Use real calendar dates in `YYYY-MM-DD` format and the status values `open`, `missing`, `submitted`, `graded`, or `excused`. Use a consistent source ID when available. Otherwise Gradebook matches progress by class, title, and due date. A snapshot replaces the previous grades and assignments in one transaction. Matching notes and check-ins stay in the database.

For an initial setup, you can also stream in a private JSON file with keys `student`, `term`, `asOf`, `grades`, and `assignments`. The last two values are arrays of objects with the CSV fields above.

```sh
docker compose exec -T gradebook python /app/server.py import < /path/to/private/snapshot.json
```

## Maintenance

- Update with `git pull --ff-only`, then `docker compose up -d --build --wait`.
- Check service status with `docker compose ps`. The health endpoint exposes availability only.
- The Compose volume `gradebook-data` holds school records and progress. For a consistent file backup, stop the service before archiving the volume and restart afterward. Keep backups private. `docker compose down` preserves the volume. Using `down -v` deletes it.
- The repository contains the application, deployment configuration, and fictional examples. Store school records, portal exports, keys, and private backups outside Git.
