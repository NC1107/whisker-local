# API

This is every endpoint the server has and what comes back from each one.
The app is the main thing that uses it, but it's plain HTTP and JSON so curl works too.

## The basics

Everything under `/api` except `/api/health` needs the access key.
Send it as `Authorization: Bearer <key>`, and a missing or wrong key gets a 401.
The key is the `ACCESS_KEY` line in `.env`.

Errors come back as `{"detail": "..."}` with a message that's fine to show a person.
A body that doesn't validate is a 422, and the detail there is a list of problems from FastAPI.
An id in a path or query that is below 1 or too big for the database to hold is a 422 as well, so it never gets as far as a lookup.

There's no `/openapi.json`, `/docs` or `/redoc`, since the schema would be served without the key and this page is the reference.
Every response, the app's own files included, carries `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` and a `Content-Security-Policy`.
The policy only lets the page load things from the server itself, plus the wasm and blob workers that Flutter's web build needs.
If a reverse proxy sets its own versions of these headers, I'd check that it replaces ours instead of adding a second set.

Times are UTC ISO 8601 strings with milliseconds, like `2026-11-08T18:00:00.000+00:00`.
Weights are in pounds, because that's what the robot reports.
Dates (`2026-11-08`) are calendar days in the server's timezone, which is the `TZ` line in `.env`.

## Health

### GET /api/health

No key needed.
Returns `{"ok": true}`, or a 503 with `{"ok": false}` if the part that talks to the robot has stopped.

## Status

### GET /api/status

Everything the dashboard shows in one request.

- `serial`: the robot's serial number.
- `online`: true when the robot has been heard from recently.
- `broker_connected`: true when the server is connected to the broker.
- `last_seen`: when the robot last sent anything, or null.
- `robot`: null until the first state arrives, then the object described below.
- `alerts`: a list of `{key, urgent, message}`, urgent ones first.
- `demo`: true when the server runs with `DEMO_MODE=1`, meaning everything in it is sample data.
  The app should say so on every screen, so sample data never gets mistaken for the real thing.
- `cats`: one cat object (see Cats below) for each cat that isn't archived, in the order they were made.
  The summary on each one already has the latest weight, the last visit and yesterday's numbers, so the home and insights tabs don't need a request per cat.
- `visits_needing_cat`: how many visits in the last 30 days have no cat yet.
- `latest_visit_needing_cat`: the newest of those, as `{id, at, weight_lb, duration_s, closest_cat_id}`, or null when there are none.
  `closest_cat_id` is the cat whose usual weight is nearest, so the "who was this" sheet can show a closest match.
  It is null when the visit has no weight or there are no cats.

The `robot` object has these.

- `status` and `status_label`: the robot's state as a code and as text for people.
- `is_cleaning`: true during a cycle.
- `cat_present`: true while the robot thinks a cat is inside.
- `drawer_percent`, `drawer_is_estimate`, `drawer_full`: the waste drawer.
- `drawer_full_lockout`: true when a full drawer has stopped the robot until someone presses Reset.
  `drawer_full` is also true then, so this is the part of it that means the robot has quit cleaning.
- `drawer_last_moved`: when the waste drawer was last pulled or pushed, or null if the server hasn't seen it yet.
- `bonnet_removed`: true while the bonnet is off.
- `litter_percent`: the last real litter reading, so it doesn't blank out during a cycle.
- `litter_level_mm`: the distance the sensor read at that same moment, which gets bigger as the litter goes down.
- `litter_source`: where `litter_percent` comes from.
  `reported` is the robot's own percentage, and when it sends one the calibration below doesn't change it.
  `calibrated` is worked out from your full reference, `learned` from the fullest reading the server has seen, and `default` is a rough guess.
  It is null until there is a reading.
- `litter_calibrated`: true when a full reference has been taken, see Litter calibration below.
- `litter_calibration`: `{full_mm, empty_mm}`, with null for a point that hasn't been taken.
- `hopper`: null until the server has seen this robot dispense from a hopper, so a robot without one never shows it.
  After that it is `{status, level_percent, level_source, fill_raw, connected, empty, last_dispensed}`.
  `status` is `ok`, `empty`, `disconnected` or `unknown`.
  `level_source` is `measured` once the server has learned this hopper's empty and full readings, and `estimate` before that, when the number can be off by a lot.
  `level_percent` and `level_source` are null if the gauge is stale, which happens after somebody refills the globe by hand.
- `clean_cycles`: the robot's own cycle counter.
- `empty_cycles`: the robot's counter of empty cycles, or null if it doesn't send one.
- `wifi_rssi`, `esp_firmware`, `pic_firmware`: connection and firmware info.
- `last_visit_at`, `last_visit_duration_s`, `last_weight_lb`: the latest visit the robot saw, whoever it was.
- `settings`: `night_light_mode`, `night_light_brightness`, `clean_cycle_wait_minutes`, `keypad_lockout`, `sleep_enabled`, `sleep_minute`, `wake_minute`.

## Events

### GET /api/events

The history log, newest first.
It takes `limit` (1 to 500, default 100) and `before`.
To get the next page, pass the `cursor` of the last event you got as `before`.
An unknown cursor is a 422.

Each event is `{id, cursor, at, kind, detail}`.
The kinds are `visit`, `clean_cycle`, `fault`, `firmware`, `drawer_moved`, `command` and `alert`, and `detail` depends on the kind.

`kind` limits it to some kinds, and you can pass it more than once, like `?kind=alert&kind=fault`.

An `alert` event is written each time an alert is raised or clears, and its `detail` is `{key, raised, urgent, message}`.
`raised` is true when it started and false when it ended, and `message` is a line that's fine to show a person.
The keys are the same ones as in `alerts` on `/api/status`.
An alert that clears before it was ever raised, like a bonnet lifted for a minute, doesn't leave anything.
The server remembers which alerts were active over a restart, so it doesn't announce them a second time.

## Actions

These are all 202 with `{"sent": true}` when the robot took them.
They fail with a 503 if the robot is offline or the broker isn't reachable, a 409 if the control lock is on, and a 504 if the robot didn't acknowledge.

- `POST /api/actions/clean` starts a clean cycle.
- `POST /api/actions/reset` presses the panel reset button.
- `POST /api/actions/refresh` asks the robot for its current state.

The one exception is calibrating the litter, which is below and answers with the status.

## Settings

### PATCH /api/settings

Send any of the fields below, and only those get changed.
Unknown fields are a 422.
It returns the robot part of `/api/status` (without `cats` and `visits_needing_cat`) once the robot has confirmed the change, or a 504 if it didn't.

- `night_light_mode`: `off`, `on` or `auto`.
- `night_light_brightness`: 0 to 100.
- `clean_cycle_wait_minutes`: 3, 7, 15, 25 or 30.
- `keypad_lockout`: true or false.
- `sleep_enabled`: true or false.
- `sleep_minute` and `wake_minute`: minutes after midnight, 0 to 1439.

## Visits

A visit is one time a cat went in, and the robot only reports a weight and a length for it.
It has no idea which cat it was, so the server works that out from the weight and keeps a label on every visit.

### The label on a visit

- `label`: `cat`, `not_cat` or `unknown`.
- `cat_id`: the cat's id when the label is `cat`, otherwise null.
- `label_source`: `auto` when the server picked it, `user` when someone set it by hand.

A visit nobody has labelled is `unknown` with source `auto`.
A `user` label is never changed by the server, whatever else happens.

The server only assigns a visit to a cat by itself when two things are true.
The weight has to be close to that cat's usual weight, and it has to be clearly closer to it than to any other cat.
Otherwise it leaves the visit as `unknown` so you can choose, which matters most for two cats that weigh about the same.
A cat's usual weight is the median of its latest 20 visits, so it follows a cat that gains or loses weight slowly.
While a cat has fewer than 3 visits, its reference weight fills in for the missing ones.
Two cats whose weights are close can't be told apart from one reading, so some of their visits will stay `unknown` and need a manual choice.
The exact numbers are named constants at the top of `server/src/whisker_local/cats.py`, with the reasoning next to each one.

Every time the cats change, or a label is set, the last 30 days of visits get looked at again, in order.
Visits older than that keep the label they had.

### GET /api/visits

Visits, oldest first.
Each one is `{id, cursor, at, kind, detail, label, cat_id, label_source}`.
`at` is when the visit ended, and `detail` has `started`, `duration_s` and `weight_lb`.
`weight_lb` is null if the robot never sent one, and those visits can only be labelled by hand.

- `days`: how far back to look, 1 to 3650, default 30.
- `cat_id`: only visits labelled as that cat.
- `needs_cat`: true for only visits with no cat yet.

Passing both `cat_id` and `needs_cat` is a 422.

### POST /api/visits/{id}/label

Sets the label on one visit, as a user label, and returns the visit.
The body is `{"label": "cat", "cat_id": 3}`, `{"label": "not_cat"}` or `{"label": "unknown"}`.
`cat_id` is required with `cat` and not allowed with the others, and getting that wrong is a 422.

`unknown` clears your label instead of saving one, and the server then goes back to deciding on its own.
An id that isn't a visit is a 404, and a cat that doesn't exist is a 404 too.
Labelling a visit with an archived cat is a 409.

## Cats

### The cat object

- `id`: stable, never reused.
- `name`: up to 40 characters.
- `color_index`: 0 for the first cat made, then 1, 2 and so on, and an archived cat keeps its number.
  It doesn't wrap, so the app should take it modulo the length of its palette.
- `breed`: free text up to 60 characters, or null.
- `birthday`: a date, or null.
  The app works out the age from it.
- `sex`: `female`, `spayed_female`, `male`, `neutered_male` or `unknown`.
- `reference_weight_lb`: what the owner said the cat weighs, or null.
  It's the starting point for the usual weight, see below.
- `estimated_weight_lb`: the same value under the name it had before the profile existed.
  It stays so an older app keeps working, and new code should use `reference_weight_lb`.
- `has_photo` and `photo_version`: whether the cat has a photo, and a number that changes whenever the photo does.
  The app can use the version to know when to fetch it again.
- `archived`: true if the cat has been put away.
- `created_at`: when it was made.
- `summary`: the numbers below.

The `summary` fields are null when there's nothing to work them out from.

- `latest_weight_lb`: the weight of this cat's newest visit that had one.
- `usual_weight_lb`: the median of its latest 20 visits.
  While it has fewer than 3, the estimate fills in the missing ones so one odd reading doesn't move it much.
- `visits_today`: visits that ended since midnight in the server's timezone.
- `visits_per_day`: visits in the last 7 days divided by 7.
  For a cat tracked for less than 7 days it divides by the days it has been tracked, and never by less than 1.
- `avg_visit_s`: the average visit length over the last 7 days.
- `weight_change_pct`: the latest weight against the usual weight, in percent, so 6.0 means 6 percent heavier.
- `last_visit_at`: when its newest visit ended.
- `yesterday_weight_lb`: the median weight of yesterday's visits, in the server's timezone.
- `yesterday_visits`: how many visits ended yesterday, 0 if none.
- `yesterday_minutes`: the time in the box yesterday, as minutes with one decimal.
- `week_visits`: visits in the seven days before today.
- `common_visit_hour`: the hour of the day, 0 to 23, with the most visits over the last 30 days.
  If two hours tie, the earlier one wins.
  Null while there are no visits.

### GET /api/cats

Cats that aren't archived, in the order they were made.
Pass `include_archived=true` to get the archived ones too.

### POST /api/cats

Makes a cat and returns it with a 201.
The body is `{"name": "Miso", "reference_weight_lb": 10.2}`, and `breed`, `birthday` and `sex` can go with it.
Only the name is needed.
The weight is optional, between 2 and 40, and it's how the server starts recognising the cat before it has any labelled visits.
Without it the cat only gets visits that you label.
`estimated_weight_lb` still works as another name for it, but sending two different values is a 422.

To make a cat from a suggestion, send the suggestion's `center_lb` as `reference_weight_lb`.
Names can't repeat among cats that aren't archived, ignoring case, and a repeat is a 409.
Making a cat looks at the last 30 days again, so recent visits at its weight get assigned straight away.

### GET /api/cats/suggestions

Where unlabelled weights bunch up, so the app can say "looks like there's a cat at about 10 lb".
It looks at visits from the last 30 days that have no cat and a weight.

- `enough_data`: true once there are at least `min_visits` of them spread over at least `min_days` days.
  Before that the clusters are probably not worth showing.
- `visits_considered`: how many visits it looked at.
- `days_of_data`: the time between the first and the last of them, in days.
- `min_visits` and `min_days`: what `enough_data` needs, so the app can show progress.
- `clusters`: a list of `{center_lb, low_lb, high_lb, visits}`, lightest first.
  Groups with fewer than 3 visits are left out.

Two cats that weigh almost the same can show up as one cluster, and then it's up to you to add the second by hand.

### GET /api/cats/{id}

One cat object.
An id that doesn't exist is a 404, and archived cats can be fetched too.

### PATCH /api/cats/{id}

Send `name`, `archived`, or both.
It returns the cat.
Archiving takes the cat out of the list and stops it getting new visits, and its automatic labels go back to `unknown` while the labels you set stay as they are.
Setting `archived` to false brings it back, as long as no other cat has taken its name.

Both only look at the last 30 days, since that's what the server relabels.
So archiving a cat wipes its automatic labels for the last 30 days, and restoring it brings them back, because the labels are worked out again from the weights.
Nothing older than 30 days changes either way.
It's on purpose, and the only thing to know is that visits from the last month show up as needing a cat while a cat is archived.

The other fields are the profile.
`breed`, `birthday` and `reference_weight_lb` can be set to null to clear them.
`name`, `archived` and `sex` can't be null, and an empty body is a 422.
A birthday in the future or before 1995 is a 422.
Changing `reference_weight_lb` looks at the last 30 days again, like making a cat does.

### GET /api/cats/{id}/series

One entry per day for the charts, with the days grouped in the server's timezone.
A visit counts for the day it ended.

- `start` and `end`: dates, both included.
  `end` defaults to today and `start` to 29 days before `end`.
- At most 366 days at a time, and `start` after `end` is a 422.

The response is `{cat_id, timezone, start, end, days}`.
Every day in the range is in `days`, including the ones with no visits.
Each day is `{date, visits, median_weight_lb, avg_visit_s}`, and the last two are null on a day with no visits.

### PUT /api/cats/{id}/photo

The body is the picture itself, with any image content type, up to 8 MB.
Anything bigger is a 413, and the server doesn't read past the limit.

The server opens it with Pillow, so a file that isn't a JPEG, PNG, WebP or GIF is a 415.
A picture that would take more than 64 MiB of memory once decoded (width times height times colour channels) is a 413, and so is one Pillow takes for a decompression bomb, before anything gets decoded.
A JPEG is checked by pixel count instead, up to 36 million, because it shrinks while it loads.
An animated picture is a 415.
Only two uploads at a time are allowed to hold their body in memory.
The others wait for up to 20 seconds without being read, and then get a 503 with a message to try again.
Decoding happens on one thread, so one picture is decoded at a time.
That is what keeps the server under its 512 MiB container limit, and the 64 MiB budget above is per picture, not for all of them together.
After that it turns the picture the right way up, drops all the metadata (the place a phone photo was taken included), scales it down to at most 1600 pixels on the long side, and saves it as a JPEG.
Transparent parts turn white.

It returns the cat, with `has_photo` true and a new `photo_version`.
The file is stored in `cat-photos` next to the database and named only from the cat's id.
The photos are not part of the database download, so back that folder up too.

### GET /api/cats/{id}/photo

The JPEG, or a 404 if the cat has none.
It needs the access key like everything else, so the app has to send the header when it loads the image.

### DELETE /api/cats/{id}/photo

Removes the photo and answers 204, also when there wasn't one.

### GET /api/cats/{id}/insights

Everything the insights screen of one cat shows, in one request.

- `period`: `week`, `month` or `year`, default `week`.
- `offset`: 0 for the current period, 1 for the one before, and so on, up to 520.

The periods are all in the server's timezone.

- `week` is the seven days ending yesterday, so a new week never starts empty.
- `month` is the 30 days ending today, and each step back is another 30 days.
- `year` is 12 calendar months ending with the current one.

The reply has these.

- `cat_id`, `period`, `offset`, `timezone`.
- `start` and `end`: the first and last day, as dates.
- `range_label`: the text for the header, like `November 1 to November 7` or `December 2025 to November 2026`.
  The year only shows up when it isn't the current one.
- `has_earlier`: true when the cat has visits before this period, so the app knows when to stop the back arrow.
  The forward arrow is off when `offset` is 0.
- `points`: the first day of each point, as dates.
  One per day, or one per month for `year`.
- `weight`, `visits` and `minutes`: each is `{series, average}`, with `series` holding one value for each entry in `points`.
- `time_of_visit`: for `week` and `month`, a list of `{day, hour, duration_s}`.
  `day` is the column, counting from 0 at `start`, and `hour` is the hour of the day as a decimal, so 19.5 is 7:30 PM.
  It's an empty list for `year`.
- `overview`: `yesterday_weight_lb`, `yesterday_visits`, `yesterday_minutes`, `week_visits` and `common_visit_hour`, the same as in the cat summary.
- `summary`: two plain sentences, like `2 visits yesterday, about average.` and `Weight is in range.`

A value in a series is null where there's no data.
That's the days that haven't come yet, the days before the cat's first visit, and for the weight, any day without a weighed visit.
A day the cat was tracked with no visits is a real 0 for `visits` and `minutes`.
`average` is the mean of the values that aren't null, and null when they all are.

For `weight`, a day is the median of that day's weights and a month is the median of the month's.
`minutes` is the time in the box, so the sum of the visit lengths for a day, and a visit with no length counts as nothing.
For `year` the `visits` and `minutes` values are per day, averaged over the days of that month so far, so the bars compare between months of different lengths.

The first summary sentence compares yesterday with the cat's visits per day over the 28 days before it.
It's "about average" within a quarter of that (and always within one visit), and "more" or "fewer than usual" outside it.
It leaves the comparison out when there are fewer than 3 days to compare with.
The second one says "in range" while the latest weight is within 7 percent of the usual weight, and "above" or "below the usual range" past that.

## Litter calibration

The litter percentage is worked out from a distance, and where "full" sits is different on every robot.
These let you tell the server what full and empty look like on yours.

### POST /api/actions/calibrate-litter

The body is `{"point": "full"}` or `{"point": "empty"}`.
Fill the globe to the line (or empty it) first, then call this.
The server asks the robot for its state, waits for the answer, and saves that distance, so it's never the cached reading from before you filled it.
It returns the same thing as `GET /api/status`.

With only a full reference, that distance counts as 90 percent and the slope is the one the robot's cloud used.
With both, the percentage runs from 0 at empty to 100 at full.
Changing it takes effect straight away, and the litter alert goes by the new number.

It fails with a 409 if there is no usable reading right now, which is during a cycle, while a cat is inside, or while the filter is being changed.
It is also a 409 if empty would read closer than full, or the other way round, since that would be a mistake.
It fails with a 504 if the robot doesn't answer, and a 503 if the robot is offline or the broker isn't reachable.
A wrong press pins the scale to the wrong place, so the app should say what it's about to do first.

### DELETE /api/actions/calibrate-litter

Throws both references away and goes back to the learned scale.
It returns the status.

## Maintenance

The robot can't tell the server when it was last cleaned or when the filter was changed, so this is a log you keep.
Marking something done only writes a line in it.
It doesn't make the robot do anything, and the Replace Filter button on the robot's own panel is still the only way to reset that.

The jobs are `filter` (the carbon filter), `deep_clean` and `globe_clean`.
How often each is due comes from the alert settings below: 30, 90 and 60 days unless you change them.
Once a job has an entry and it's due, the matching alert (`filter_due`, `deep_clean_due`, `globe_clean_due`) shows up in `alerts` and in the history.
A job with no entry is never due, since the server can't know you aren't tracking it.

### GET /api/maintenance

Returns `{today, items, log}`.

- `today`: the date in the server's timezone.
- `items`: one per job, in the order above.
  Each is `{kind, label, interval_days, last_done, due, days_until_due, is_due}`.
  `last_done` and `due` are dates, or null when the job has never been logged.
  `days_until_due` is negative once it's late, and `is_due` is true from the due date on.
- `log`: the entries, newest first, as `{id, kind, label, done_on, recorded_at}`.
  `limit` is how many, 1 to 500, default 50.

`last_done` is the newest date logged, so adding an older one by hand doesn't push it back.

### POST /api/maintenance

The body is `{"kind": "filter"}`, with an optional `done_on` date for something you did earlier.
It defaults to today, and a date in the future or before 2020 is a 422.
It returns the entry with a 201.
Logging the same job for the same day twice returns the existing entry with a 200, so a double tap doesn't make two lines.

### DELETE /api/maintenance/{id}

Removes one entry, for when you tapped the wrong job.
It's a 204, or a 404 if there's no such entry.

## Alert settings

These are the thresholds the alerts use.
The defaults are the ones in the whiskerless library, and they're listed in `defaults` so the app can offer a reset.

- `drawer_level_percent`: the drawer alert at this fill level or more, 1 to 100, default 80.
- `litter_low_percent`: the low litter alert at this level or less, 1 to 99, default 30.
- `no_visit_hours`: the no-visit alert after this many hours without a cat, 1 to 720, default 24.
- `filter_days`, `deep_clean_days` and `globe_clean_days`: how many days after a job is logged it's due again, 1 to 3650, default 30, 90 and 60.

The level alerts clear again only once the level is 10 points back past the threshold, so a gauge that wobbles doesn't keep raising it.
The library has more settings (long visits, visits per hour, weight change, reminder hours), but those are for notifications and nothing here acts on them, so they're not offered.

### GET /api/alert-settings

Returns `{settings, defaults}`.

### PATCH /api/alert-settings

Send any of the fields and only those change.
An empty body, an unknown field or a value out of range is a 422.
It returns `{settings, defaults}`.
Only what you set on purpose is stored, so a default the library improves later still applies to anything you didn't touch.

### DELETE /api/alert-settings

Puts everything back to the defaults and returns `{settings, defaults}`.

## Insights

### GET /api/insights

Counts for a range of days, worked out on the server, so a range of years is still a small reply.
Days, weeks and hours are in the server's timezone, so a visit at 23:30 local counts for that evening.

- `start` and `end`: dates, both included.
  `end` defaults to today and `start` to 29 days before it.
- `days`: shorthand for the last N days up to today, 1 to 3660.
  Using it together with `start` or `end` is a 422.
- `granularity`: `day`, `week` (starting Monday) or `month`.
  The default is `day` up to 92 days, `week` up to 730 and `month` after that.

A range can be at most 3660 days, and a step that would give more than 800 points is a 422, so ask for a wider one.
The first and last point of a week or month series can be partial, since only days inside the range are counted.

The reply has these.

- `timezone`, `start`, `end`, `days`, `granularity`.
- `previous_start` and `previous_end`: the period right before, with the same number of days.
- `overall`: all the cats together, see below.
- `cats`: one entry per cat with the same fields as `overall` plus `cat_id`, `name`, `color_index` and `archived`.
  Cats that aren't archived are always listed, and an archived one only when it has visits in this or the previous period.
- `unassigned`: the same fields again, for visits with no cat yet.

Visits you've labelled `not_cat` aren't counted anywhere.

Each of `overall`, a cat and `unassigned` has these.

- `visits`, `visits_per_day` and `avg_visit_s` for the range.
  `avg_visit_s` only counts visits that have a length, and it's null when none do.
- `hours`: 24 visit counts, for the hours 0 to 23.
- `series`: one `{date, visits, avg_visit_s}` per day, week or month, including the empty ones.
  `date` is the first day of the step.
- `previous`: the same totals for the previous period.
- `change`: `visits_pct` and `avg_visit_pct`, which are percent change against the previous period, or null if the previous one was zero or empty.

`overall` also has `clean_cycles` and `clean_cycles_per_day`, a `clean_cycles` count in each point of `series`, and `clean_cycles` in `previous` and `clean_cycles_pct` in `change`.
Cycles aren't tied to a cat, so the per cat entries don't have them.

## Export

Both of these need the access key like everything else, so a browser has to fetch them with the header and save the bytes, since a plain link can't send it.

### GET /api/export/visits.csv

Every visit as CSV, oldest first, with a header row.
`start` and `end` are optional dates, both included, in the server's timezone.

The columns are `id`, `started_at`, `ended_at`, `local_date`, `local_time`, `duration_s`, `weight_lb`, `cat`, `cat_id`, `label` and `label_source`.
The two times are UTC like everywhere else, and the local ones are for people reading it in a spreadsheet.
A missing value is an empty cell.
A cat name that starts with `=`, `+`, `-` or `@` gets a `'` in front, so a spreadsheet doesn't treat it as a formula.

### GET /api/export/database

A copy of the whole SQLite database, raw messages and all, as one file.
It's made with SQLite's backup function, so it's consistent even while the robot is writing, and it includes anything still in the write-ahead log.
Copying the file out of the data folder while the server runs isn't safe, which is the reason this exists.
It can be big, since it has the raw messages, which are kept for up to 90 days.
Cat photos are in their own folder and aren't in it.
To restore it, stop the server and put the file where `DB_PATH` points.

## Limits on what gets stored

The robot's port is open to anyone on the network, and the serial isn't a secret since a client can learn it by subscribing, so the server doesn't trust that what arrives is a robot.

- A message over 16 KB is dropped and logged before it's read.
  The broker already refuses bigger ones, and the robot's own documents are about a kilobyte.
- A message with text that can't be written as UTF-8 (a lone surrogate like `\ud800`) is dropped and logged before anything is stored.
- A firmware version is only recorded or shown when it matches `[0-9A-Za-z._-]{1,64}`, otherwise it is left out.
- Raw messages are saved at up to 1 a second on average, with room for a burst of 120.
  The rest are logged (once a minute, with a count) and not saved, though they are still read like any other message.
- Raw messages older than 90 days are deleted, and so are the oldest ones past 200 MB in total.
  It runs when the server starts and then every hour.

Only the raw message table is ever pruned.
Visits and every other event stay.

A visit that ended less than 15 seconds before the server stopped is kept when it starts again, since it waited for its weight.
The weight itself only lives in memory, so that visit comes back without one, and it can be labelled by hand like any other.
