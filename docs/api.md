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
- `last_clean_at`: when the most recent clean cycle finished, or null if none is recorded.
- `robot`: null until the first state arrives, then the object described below.
- `alerts`: a list of `{key, urgent, message}`, urgent ones first.
- `demo`: true when the server runs with `DEMO_MODE=1`, meaning everything in it is sample data.
  The app should say so on every screen, so sample data never gets mistaken for the real thing.
- `push_configured`: true when the server has `PUSH_RELAY_URL` set, meaning it can send push notifications.
  The app only asks for notification permission when this is true.
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
- `litter_is_estimate`: true when `litter_source` is `learned` or `default`.
  Then the percentage is relative to the fullest level the server has seen and not a measurement, and the app should say so.
- `litter_calibrated`: true when a full reference has been taken, see Litter calibration below.
- `litter_calibration`: `{full_mm, empty_mm}`, with null for a point that hasn't been taken.
- `hopper`: null until the server has seen this robot dispense from a hopper, so a robot without one never shows it.
  After that it is `{status, level_percent, level_source, fill_raw, connected, empty, last_dispensed}`.
  `status` is `ok`, `empty`, `disconnected` or `unknown`.
  `level_source` is `measured` once the server has learned this hopper's empty and full readings, and `estimate` before that, when the number can be off by a lot.
  `level_percent` and `level_source` are null if the gauge is stale, which happens after somebody refills the globe by hand.
  The hopper is read-only: the robot gives no way to turn it on or off remotely, so there is no endpoint for it.
  `connected` turns true when litter is first dispensed, and the robot has no signal that reports it unplugged.
- `clean_cycles`: the robot's own cycle counter.
- `empty_cycles`: the robot's counter of empty cycles, or null if it doesn't send one.
- `wifi_rssi`, `esp_firmware`, `pic_firmware`: connection and firmware info.
- `last_visit_at`, `last_visit_duration_s`, `last_weight_lb`: the latest visit that was saved, whoever it was.
  They come from saved visits only, so a cleaning cycle, a hand in the globe or the sensors moving does not change them.
  The no-visit alert counts from `last_visit_at` too.
- `settings`: `night_light_mode`, `night_light_brightness`, `clean_cycle_wait_minutes`, `keypad_lockout`, `sleep_enabled`, `sleep_minute`, `wake_minute`.

## Events

### GET /api/events

The history log, newest first.
It takes `limit` (1 to 500, default 100) and `before`.
To get the next page, pass the `cursor` of the last event you got as `before`.
An unknown cursor is a 422.

Each event is `{id, cursor, at, kind, detail}`.
The kinds are `visit`, `clean_cycle`, `fault`, `firmware`, `drawer_moved`, `command` and `alert`, and `detail` depends on the kind.

An imported event has `imported`, `import_batch` and `imported_at` in its `detail`, see Import below.

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

A visit is an episode, not a weight row.
It is saved when the robot reports a cat inside (status 25, with the sight line broken) or when a weight arrives, whichever comes first.
A change in the `catDetect` sensor on its own is never a visit, because the globe raises it in every cycle.
A weight that arrives after the visit was saved is added to that visit, and a repeat of it is ignored.
Two weights in one stretch more than 1.2 lb apart are two visits, and closer than that they are one.
A load on the scale with no sight line broken, and a stretch of more than 20 minutes, are not given a duration.
Imported history counts the same way, see Import below.

### The label on a visit

- `label`: `cat`, `not_cat` or `unknown`.
- `cat_id`: the cat's id when the label is `cat`, otherwise null.
- `label_source`: `auto` when the server picked it, `user` when someone set it by hand.
- `identity`: how sure the server was, see below, or null for a visit it has not looked at yet.

A visit nobody has labelled is `unknown` with source `auto`.
A `user` label is never changed by the server, whatever else happens, and it counts as the strongest evidence about what a cat weighs.

The server names a cat for a visit only when it is at least 90 percent sure.
Each cat has a bell curve around its weight, and the chance of each cat for a reading comes from those curves plus a small allowance for readings that fit nobody.
Anything less than 90 percent is left as `unknown`, so you can choose, and the visit still counts in the household totals.
`identity` is `{status, candidate_cat_id, confidence}`.

- `confident`: named for a cat, `confidence` is the chance.
- `unsure`: between two cats, `candidate_cat_id` is the more likely one and `confidence` its chance, which is below 0.9.
- `off_cluster`: too far from every cat to be one of them, like 17 lb or 5.8 lb.
- `unsettled`: a cat was named at first, and the name was withdrawn because another cat has gone quiet, see below.
- `no_weight`: the robot sent no weight, so nobody can tell from the scale.

A hand label has `label_source` `user` and its `identity` stays whatever it was.

Two things are kept apart.
A slow tracker follows each cat's usual weight so that labels keep working as a cat changes.
Toward another cat it moves at most half a percent a week, and away from every other cat it follows freely.
The anchor is a frozen weight per cat, see below, and it is what a health trend is measured against, so a cat that loses weight cannot drag its own baseline down.

A cat with an anchor that has not been named for about five of its usual visits is silent.
While it is silent, other cats' names in the range it could have fallen to (from 70 percent of its anchor up to a little above it) are withdrawn and the visits become `unsettled`.
Without that a cat that loses weight into its sibling's range would be named as the sibling.
Once the silent cat is named again the withdrawal stops.
Days with no data from the robot do not count toward a cat being silent.

Visits in a range marked unreliable are named, but never teach a tracker or set an anchor.
Every time the cats change, or a label is set, all the visits are replayed in order and the ones from the last 30 days are written.
Visits older than that keep the label they had.
The first time a new version starts, the whole history is named afresh once; hand labels stay.

### GET /api/visits

Visits, oldest first.
Each one is `{id, cursor, at, kind, detail, label, cat_id, label_source, identity}`.
`at` is when the visit ended, and `detail` has `started`, `duration_s` and `weight_lb`.
`weight_lb` is null if the robot never sent one, and those visits can only be labelled by hand.
`duration_s` is null when the robot gave none, when it was more than 20 minutes, and for imported visits.
A live visit also has `origin: live`, `weight_raw` (the robot's number before it is divided by 100), `settled_s` (seconds of settled weight), `readings` (how many weights it came from), `source` (`status`, `activity` or `weight`) and `long_stay` when it was over 20 minutes.
An imported visit has `readings`, `detected_at` and `definition: 2` instead.

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
A day on which the robot was not heard is not a day with no visits, and the fields below say null for it.

- `latest_weight_lb`: the weight of this cat's newest visit that had one.
- `usual_weight_lb`: the cat's anchor, or its reference weight until it has one.
  It does not follow the cat.
- `anchor_weight_lb`: the frozen anchor, or null until the cat has one.
- `recent_weight_lb` and `recent_readings`: the median of the cat's named weights over the last 14 days and how many there are.
  The median is null below 3 readings.
- `visits_today`: visits that ended since midnight in the server's timezone.
- `visits_per_day`: visits in the last 7 days divided by the number of those days that are full and not marked unreliable.
  Null when there are none, and for a cat with no visits.
- `avg_visit_s`: the average visit length over the last 7 days.
- `weight_change_pct`: `recent_weight_lb` against `anchor_weight_lb`, in percent, so 6.0 means 6 percent heavier.
  It is null below 8 readings in the 14 days, because the scale is off by 3 to 4 percent on each reading.
- `last_visit_at`: when its newest visit ended.
- `yesterday_weight_lb`: the median weight of yesterday's visits, in the server's timezone.
- `yesterday_visits`: how many visits ended yesterday, or null if the robot was not heard at all yesterday.
- `yesterday_minutes`: the time in the box yesterday, in minutes with one decimal.
  It is null when the day has no data, and when any of that day's visits has no length, since a partial sum would be wrong.
  A full day with no visits is 0.
- `yesterday_coverage`: `full`, `partial` or `none`, see Coverage below.
- `week_visits`: visits in the seven days before today, or null if none of them has data.
- `week_days_with_data`: how many of those seven days have any data.
- `common_visit_hour`: the hour of the day, 0 to 23, in which most visits started over the last 30 days.
  It is null unless that hour has at least a quarter of the visits and at least 3, and more than any other hour.
  A tie, or a spread out day, is no habit.

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
Each day is `{date, visits, median_weight_lb, avg_visit_s, coverage, unreliable}`.
`visits` is null on a day with no data from the robot, and 0 on a day the robot was heard and the cat did not visit.
The weight and the length are null on a day with no visits.
`coverage` is `full`, `partial` or `none`, and `unreliable` is true on a day inside a range marked unreliable.
The days stay in the series either way, so a chart can hatch them.

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

- `period`: `week`, `month`, `year` or `all`, default `week`.
- `offset`: 0 for the current period, 1 for the one before, and so on, up to 5200.
  There is no other limit on how far back it goes.
  A page before the year 1 is a 422.

The periods are all in the server's timezone.

- `week` is the seven days ending yesterday, so a new week never starts empty.
- `month` is the 30 days ending today, and each step back is another 30 days.
- `year` is 12 calendar months ending with the current one.
- `all` is every calendar month from the cat's first visit to the current one, at most 1200 of them, and ignores `offset`.
  It is shaped like `year`.

The reply has these.

- `cat_id`, `period`, `offset`, `timezone`.
- `start` and `end`: the first and last day, as dates.
- `range_label`: the text for the header, like `November 1 to November 7` or `December 2025 to November 2026`.
  The year only shows up when it isn't the current one.
- `tracked_from`: the date of the cat's first visit, or null.
- `has_earlier`: true when the cat has visits before this period, so the app knows when to stop the back arrow.
  The forward arrow is off when `offset` is 0.
- `points`: the first day of each point, as dates.
  One per day, or one per month for `year` and `all`.
- `coverage`: `full`, `partial` or `none` for each entry in `points`, and `unreliable`: true for each entry that touches a range marked unreliable.
- `weight`, `visits` and `minutes`: each is `{series, average}`, with `series` holding one value for each entry in `points`.
- `time_of_visit`: for `week` and `month`, a list of `{day, hour, duration_s}`.
  `day` is the column, counting from 0 at `start`, and `hour` is the hour of the day as a decimal, so 19.5 is 7:30 PM.
  It's an empty list for `year` and `all`.
- `overview`: `yesterday_weight_lb`, `yesterday_visits`, `yesterday_minutes`, `yesterday_coverage`, `week_visits`, `week_days_with_data` and `common_visit_hour`, the same as in the cat summary.
- `summary`: two plain sentences, like `2 visits yesterday.` and `Latest weight reading 10.1 lb.`
  They state what was recorded and make no comparison.
  A day with no data reads `No data from the robot yesterday.`, and a partly heard one says the robot was not heard all day.

A value in a series is null where there's no data.
That's the days that haven't come yet, the days before the cat's first visit, the days the robot was not heard (coverage `none`), and for the weight, any day without a weighed visit.
A day the robot was heard and the cat did not visit is a real 0 for `visits`, and for `minutes` too.
`average` is the mean of the days that are full and not marked unreliable, and null when there are none.
A partial day and an unreliable day keep their value in the series and are left out of `average`.

For `weight`, a day is the median of that day's weights and a month is the median of the month's.
`minutes` is the time in the box for a day, the sum of the visit lengths.
It is null when any visit that day has no length, which is the case for all imported history.
For `year` and `all` the `visits` and `minutes` values are per day, averaged over the full, reliable days of that month, and null for a month with none.

## Anchor

A cat's anchor is the weight its health is measured against.
It is the median of the cat's first 12 named readings, taken once they span 5 days, and then it stays where it is.
It comes with `spread_lb` (the scatter of those readings), `visits`, `visits_per_day` (how often the cat was seen, used to tell when it goes quiet), `set_at` and `source`.
`source` is `first readings`, `re-anchored` or `owner`.

### PUT /api/cats/{id}/anchor

Sets the anchor again, for a new diet or after a visit to the vet, and returns it.
The body is `{"weight_lb": 9.8}` to set it by hand, or `{}` to take it from the cat's latest 20 named readings.
Taking it from readings with fewer than 12 of them is a 409, and a weight outside 2 to 40 is a 422.
The last 30 days are named again afterwards.

### DELETE /api/cats/{id}/anchor

Forgets the anchor and answers 204.
The whole history is named again, and the anchor forms again from the cat's first readings.

### GET /api/identity

How well the recent visits could be named.
`days` is how far back to look, 1 to 366, default 30.

- `visits`: visits in that time, not counting ones marked `not_cat`.
- `named`: the ones named for a cat, by the server with confidence or by the owner.
- `not_sure`: `unsure`, `off_cluster` and `unsettled` together.
- `unsettled`: the part of those that were named at first and then withdrawn.
- `no_weight`: visits with no weight.
- `by_status`: the count for each of `confident`, `user`, `unsure`, `off_cluster`, `unsettled` and `no_weight`.
- `silent_cats`: cats with an anchor that are quiet now, as `{cat_id, since, until, open, days}`.
- `anchors`: the anchor of each cat by id.

## Coverage

The server records when the robot goes online and offline, from its messages.
A day is `full` when the robot was heard for at least 90 percent of it, `none` when it was heard for under 5 percent and nothing happened, and `partial` otherwise.
The day in progress is always partial.
Days before the first record are judged from events instead: a gap of 36 hours or more with no visit, cycle or other robot event counts as a time nobody was listening.
This is how the two empty days in an imported history show up as `none` and not as zero visits.
A restart of the server closes the online period at the last message, so the time it was down is not covered.

### GET /api/coverage

The days of a range, as `{timezone, start, end, days, full, partial, none, unreliable_ranges}`.
Pass `start` and `end` as dates, or `days` for the last N days, 1 to 366.
The default is the last 30 days, and more than 366 days is a 422.
Each day is `{date, state, source, heard_share, events, unreliable}`.
`source` is `online_record` when the record covers any of the day and `inferred` otherwise.
`heard_share` is the part of the day the robot was heard, `events` counts the visits and cycles, and `unreliable` is `{id, note}` or null.

### POST /api/coverage/unreliable

Marks a range of dates as unreliable, for example when the box was moved somewhere its scale reads wrongly, and answers 201 with the range.
The body is `{"start": "2026-11-02", "end": "2026-11-08", "note": "box in a cabinet"}`, and the note is 1 to 200 characters.
The end before the start, and a missing note, are a 422.
Those days stay in every series and chart with `unreliable` set.
They are left out of averages and baselines, and of trackers and anchors.

### GET /api/coverage/unreliable

The marked ranges, as `{id, start_date, end_date, note, created_at}`.
It is also in `unreliable_ranges` of `/api/coverage`.

### DELETE /api/coverage/unreliable/{id}

Removes a range and answers 204, or 404 if there is none.

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

## Push notifications

Push is optional and does nothing unless the server has `PUSH_RELAY_URL` set.
Without it, every route below except `GET /api/push` answers 404 and `GET /api/push` reports `configured: false`.
All of them need the access key like any other route.

The server never talks to Apple or Google.
It sends the text of a raised alert, plus the phone tokens it was given, to the relay.
Only alerts that start are sent, not the ones that clear.
Nothing is queued for later: at most 16 alerts wait while the relay is slow, the oldest is dropped past that, and a relay that is down never delays the robot.

### GET /api/push

Returns `{configured, devices, kinds, defaults}`.
`devices` is how many phones are registered.
`kinds` maps every alert key to true or false, and `defaults` is the same map before any change.
The routine ones (`visit`, `long_visit`, `frequent_visits`, `cycle_completed`, `panel_lock_changed`) default to off.

### PUT /api/push/devices

The body is `{"token": "...", "platform": "ios"}` where platform is `ios` or `android`.
The token is the one the platform gave the phone, 1 to 1024 printable characters with no spaces.
For `ios` it is the APNs device token in hex.
The relay does not deliver to `android` yet, so the app only registers `ios` phones.
It's a 204, and registering the same token again changes nothing.
The server keeps the 10 newest phones.

### DELETE /api/push/devices

The body is `{"token": "..."}`.
It's a 204 whether or not the token was known.
The server also drops a token by itself when the relay says it is dead or belongs to another server.

### PATCH /api/push/kinds

The body is `{"kinds": {"drawer_full": false, "visit": true}}` with any alert keys from the lists above.
An unknown key is a 422.
It returns the same object as `GET /api/push`.

### POST /api/push/test

Sends a test message to every registered phone, whatever the kinds say, and returns `{"delivered": 1}`.
It's a 409 when no phone has registered yet, and a 502 when the relay can't be reached or refuses.

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
- `coverage`: `{full, partial, none, unreliable}`, how many days of the range are of each kind.
- `previous_start` and `previous_end`: the period right before, with the same number of days.
- `overall`: all the cats together, see below.
- `cats`: one entry per cat with the same fields as `overall` plus `cat_id`, `name`, `color_index` and `archived`.
  Cats that aren't archived are always listed, and an archived one only when it has visits in this or the previous period.
- `unassigned`: the same fields again, for visits with no cat yet.

Visits you've labelled `not_cat` aren't counted anywhere.

Each of `overall`, a cat and `unassigned` has these.

- `visits`, `visits_per_day`, `days_counted` and `avg_visit_s` for the range.
  `visits` is the raw count.
  `visits_per_day` is over the days that are full and not marked unreliable, `days_counted` is how many there are, and the rate is null when there are none.
  `avg_visit_s` only counts visits that have a length, and it's null when none do.
- `hours`: 24 visit counts, for the hours 0 to 23.
- `series`: one `{date, visits, avg_visit_s, coverage}` per day, week or month.
  `date` is the first day of the step.
  `visits` is null, and so is `avg_visit_s`, when the robot was not heard on any day of the step, and 0 when it was heard and nothing happened.
- `previous`: the same totals for the previous period.
- `change`: `visits_pct` and `avg_visit_pct`, percent change against the previous period.
  `visits_pct` compares the per-day rates, and it is null unless both periods have at least 20 visits and a similar number of counted days (within 25 percent).
  `avg_visit_pct` is null unless both have at least 20 visits with a length.

`overall` also has `clean_cycles` and `clean_cycles_per_day`, a `clean_cycles` count in each point of `series` (null where `visits` is), and `clean_cycles` in `previous` and `clean_cycles_pct` in `change`.
Cycles aren't tied to a cat, so the per cat entries don't have them.

## Early-warning checks

These routes back the Worth a look list and its charts.
They are an early-warning aid built on the box scale and the visit counts, never a diagnosis.
Days with no data from the robot are never counted as zero visits, and days marked unreliable or flagged for a disturbed scale are left out of every check.
All dates are in the server's timezone.

### GET /api/worth-a-look

The notes that have fired, and the checks that cannot run yet.

- `cat_id`: only this cat's notes and the household ones.
- `through`: the last finished day the checks looked at.
- `notes`: a list of `{key, kind, cat_id, level, title, body, since}`.
  `kind` is `weight_change`, `weight_slope`, `household_rate`, `cat_rate`, `silence`, `burst` or `rhythm`.
  `level` is `urgent` (a burst of short visits by a male cat), `look` or `info` (rhythm notes, never pushed).
  `since` is the date the change started, or null.
  `title` and `body` are plain sentences that say what changed and make no medical claim.
- `waiting`: a list of `{kind, cat_id, ready, text}`, one per check.
  A check that is not `ready` says how much more history it needs.

The checks and what they ask for.

- `weight_change`: the median of the last 14 days is at least 4 percent from the cat's anchor and at least 3 standard errors away, with at least 8 readings, on 3 days in a row.
  It needs 42 days since the cat's first reading and 15 readings.
  The weight line itself needs 21 days.
- `weight_slope`: a Theil-Sen slope over 90 days whose 99 percent interval leaves out zero and is at least 0.5 percent a month, held 7 and 14 days earlier too.
  It needs 90 days.
- `household_rate`: visits in 7 days against the 28 days before, with a gamma-Poisson predictive, at least 1.75 times or at most 0.57 times as many, p below 0.01, a CUSUM crossing and 3 days in a row.
  It needs 21 days.
- `cat_rate`: the same over 14 days for one cat, needing 35 days.
- `silence`: no visit for more than the larger of 24 hours and 1.5 times the longest earlier quiet stretch, only if the robot was online throughout.
  It needs 14 days.
- `burst`: 4 or more visits by one cat in 3 hours, all with live durations shorter than the cat's earlier 25th percentile, for a cat that normally visits less than 1.3 times a day.
  It needs 10 earlier timed visits, so imported history can never raise it.
- `rhythm`: a 3-hour block whose share of visits moved at least twofold over 4 weeks against the 8 weeks before.
  It needs 8 weeks.

Rate, weight change and the household checks are also filtered together with Benjamini-Hochberg at q 0.1.

### on_irregularity

Every note of level `urgent` or `look` calls `whisker_local.health.on_irregularity(cat, kind, title, body)` once, when it first appears.
`cat` is the cat row or null for the household.
The default only logs.
The server checks every 30 minutes and remembers what it has announced, so a note that stays does not repeat.

### GET /api/data-quality

Which days can be trusted.

- `start` and `end`: dates, at most 3660 days apart.
  They default to the last 90 days.
- `days`: one entry per day with `state` (`full`, `partial` or `none`), `heard_share`, `unreliable` (the marked range or null), `scale_disturbed`, `visits`, `named` and `unnamed`.
  A day is scale disturbed when at least 5 of its weighed visits fit no cat and 30 percent or more are far from every cat.
- `gaps`: runs of days as `{start, end, kind}` where `kind` is `no_data`, `scale` or `unreliable`.
- `summary`: counts for the range, with `unnamed_share`, the share of visits not matched to a cat with confidence.
- `earliest`: the date of the first visit, so the app knows when to stop.
- `unreliable_ranges`: every range marked unreliable.

### GET /api/cats/{id}/weight-chart

The data for the weight chart, for `start` to `end` as above.

- `readings`: every weighed visit as `{date, hour, lb, hollow, why}`.
  A reading on an unreliable day, or far from the cat's usual, is `hollow` with a `why` and is kept in view.
- `line`: per day with data, `{date, median, low, high}` for the 14-day median and its spread.
  It is empty before 21 days, and days with no data are missing so the line breaks.
- `anchor`, `bands` (the gaps as for `gaps` above), and `latest` with `stage` (`none`, `trend` or `change`), `median_lb`, `readings`, `change_pct`, `range_pct` and `noise_pct`.
- `status`: `{line, text}` where `text` says what is still missing, or null.
- `slope`: the 90-day drift when it qualifies, else absent or null.

### GET /api/rhythm

When a cat, or the household without `cat_id`, uses the box.

- `actogram`: the newest 90 days or fewer of `start` to `end`, each `{date, state, unreliable, visits}`.
  A visit is `{hour, minutes, cat_id, sure}`, and `sure` is false when no cat was named with confidence.
- `profile`: eight 3-hour blocks with the share of the last 28 days and the share of the 56 before them.
- `heatmap`: `{ready: false, need_days}` until 8 weeks, then `{ready: true, rows}` with visits per week for each weekday and 6-hour block.

### Paging back through history

`GET /api/events` already pages with `before`, the `cursor` of the oldest event received, so every event can be reached.
The Activity list asks for 300 at a time and offers older ones until a page comes back short.

## Import

Brings in the activity export from the Whisker app, so the history from before the robot joined this server shows up in the app.
Imported rows are written like live ones, so Activity, Insights and the cat summaries count them with no special cases.
They are marked with `"imported": true` and an `"import_batch"` id in their `detail`, and an imported visit has no `started` or `duration_s`, so its time in the box is unknown and not 0.

### POST /api/import/whisker-activity

The body is the CSV file as it came out of the Whisker app, with the header `Activity,Timestamp,Value`.
The file can be up to 2 MB.
Any other kind of file gets a 422 with a plain message.

- `dry_run`: true to only count, writing nothing.
  The real run returns the same summary.
- `reference_date`: the date the file was exported, like `2026-11-08`.
  The file has no years, so a date after this one is taken to be from the previous year.
  It defaults to today in the server's timezone, and a date in the future is a 422.

Times in the file are read in the server's timezone.
The hour that repeats when the clocks go back is told apart by the order of the rows.
The file is newest first, so a time in that hour is taken as the second pass unless that would put it after the row above it.

What each row becomes.

- `Cat detected`, with the `Weight recorded` rows that follow it: one `visit`.
  A weight belongs to the latest detection before it, if that was at most 15 minutes earlier.
  Weights of one detection less than 1.2 lb apart are one visit with their average, and further apart they are two visits.
  A detection with no weight is a visit with no weight, so no cat, and it is counted in `visits_without_weight`.
- `Weight recorded` with no detection: a `visit` of its own.
- `Clean Cycle Complete`: a `clean_cycle`.
- `Clean Cycle In Progress` and `Cycle interrupted`: counted, not stored.

Importing a file twice adds nothing the second time.
Nothing at or after the first thing the robot itself reported (its first raw message or event) is imported, so an import can't count a visit twice.

The summary is `{dry_run, batch_id, reference_date, rows_read, visits, visits_without_weight, visits_matched_to_cat, clean_cycles, first_at, last_at, already_imported, after_live_data, live_data_starts_at, cat_detections_without_weight, cycles_interrupted, cycles_in_progress, unreadable_rows}`.
`visits` and `clean_cycles` are the new ones this run adds, and `first_at` and `last_at` span them.
`batch_id` and `visits_matched_to_cat` are null on a dry run, and `batch_id` is also null when there was nothing new to add.

`cat_detections_without_weight` is every visit in the file that has no weight, not only the new ones.

An imported visit that fits no cat shows in Activity, but it isn't counted in `visits_needing_cat` and is never offered as `latest_visit_needing_cat`.
A visit that came from the robot still is.
`GET /api/visits?needs_cat=true` lists both.

### Reprocessing history imported the old way

Before episodes, an import made one visit for each weight row.
Posting the same file with `reprocess=true` brings that history to the episode rule, and `dry_run=true` only counts.
The file is needed again because the detections were never stored.

Visits are matched by the time of their weight rows, so ids and labels survive.
Two rows of one detection become one visit, and the extra one is removed.
If the removed one had a hand-made label and the one kept had none, the label moves to the one kept.
A detection with no weight is added as a new visit, in a new import batch that can be undone like any other.
Visits from the robot and anything outside the file are left alone.
Running it again changes nothing.

The reply is `{dry_run, batch_id, reference_date, visits_before, visits_after, merged, added_without_weight, added_with_weight, updated, unchanged, labels_moved, backup, first_at, last_at}`.
`backup` is the name of a copy of the whole database written to the `backups` folder next to it before anything changed, and it is null on a dry run.
Restoring that file puts everything back, as long as the server is stopped first.

### GET /api/import/batches

Every import still in the history, newest first, as `{batch_id, imported_at, visits, clean_cycles, first_at, last_at}`.

### DELETE /api/import/batches/{batch_id}

Removes every event of that import and any label on its visits, and leaves everything the robot recorded alone.
Returns `{batch_id, removed}`, or a 404 if there is no such import.
Live visits are matched to cats again afterwards, without the imported history.

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

The server writes its own copies to a `backups` folder next to the database, and never deletes them.
One is made before the first start on a new version that adds tables to a database with history in it, named `whisker-local-before-upgrade-<time>.db`.
Another is made before every reprocess.
The new tables are `robot_presence`, `unreliable_ranges`, `visit_identity` and `cat_anchors`.
No existing table or row is changed by the upgrade, so dropping the new tables returns a database to its earlier shape.

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

A visit that ended less than 15 seconds before the server stopped is kept when it starts again, with the weights it had, since it waited for its weight.
A visit that was still going when the server stopped is lost.
The weight itself only lives in memory, so that visit comes back without one, and it can be labelled by hand like any other.
