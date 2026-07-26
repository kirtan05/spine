---
title: "The layers don't exist"
subtitle: "Building a reading tracker, and finding out most of my reading history is unrecoverable"
date: 2026-07-26
tags: [self-hosting, cloudflare, koreader, data]
---

I read a lot and I have almost no record of it.

Play Books knows, presumably, but it won't tell me. There's no reading journal, no
time-based stats, no list of what I finished and when. I've been reading through Malazan
and Cradle and a dozen other series on that app for years, and the total artifact of all
of it is a grid of covers sorted by whatever "recent" means.

So I set out to build the thing I wanted. That went fine. What surprised me was the part
where I tried to recover the past.

## The easy part

The architecture came together quickly once I noticed the right split. Reading position
is a single number. It cannot be merged — if two devices disagree, you don't reconcile
them, you *choose* one. Reading sessions are the opposite: append-only per device, and
merging them is trivial because no two devices ever write the same row.

Those two shapes want different homes. Position needs to be reachable at the moment I
open a book, which is often on a train with my laptop asleep at home. Sessions can catch
up whenever.

The usual self-hosted setup puts both on the same box as the library, which is exactly
backwards. Your library server is large and sleepy. Your sync endpoint needs to be
neither — it's four HTTP routes and a table. One popular implementation is under a
thousand lines in a single file.

I already host a static site on Cloudflare. So the sync endpoint went there: a Worker,
a D1 database, four routes implementing KOReader's stock sync protocol so my devices
needed nothing but a URL change. The library stays on the laptop, where it can sleep as
much as it likes.

That's the whole insight, and it's a small one: put the unmergeable thing on the cheapest
node you can keep awake.

## The part that doesn't work

Then I got ambitious. I wanted the history — not just from now on, but backwards.

The reasoning felt sound. Play Books orders the library by last-opened, which gives me a
sequence. Chrome's download database has a timestamp for every file I ever added, which
gives me a start date. Digital Wellbeing knows how many minutes I spent in Play Books
each day. My search history presumably lights up around whatever I was reading. Stack
those layers and reconstruct the timeline.

Here's what each layer actually gives you.

Download timestamps are real. That's a genuine observation with a precise time, sitting
in a SQLite file I already have. Combined with last-opened ordering, I get an interval
per book: acquired here, last touched there. For someone who reads fast, that interval
is often tight. Good signal, nearly free.

Search history is worse than useless. I don't search for most of what I read. When I do
search, it's scattered — research before starting, arguments months after finishing.
Matching queries to titles requires fuzzy entity resolution, which is the most work in
the whole plan for the least return, and it would produce results confident enough to
believe.

Wellbeing data is real but shallow. It's per-app, so it can tell me I spent 47 minutes
in Play Books on a Tuesday but not which book. And retention depends on the bucket you
ask for: daily figures survive about a week, monthly ones about six months — nothing
survives at useful granularity for the years I actually care about. There's no export
button either; you get it out with `adb shell dumpsys usagestats` and a parser.

You can allocate those daily minutes across the books whose intervals contain that day,
weighted by remaining pages. That's a legitimate method. It's also, unmistakably, a
model — a number I invented from a plausible assumption, not a number anybody measured.

And for everything before the Wellbeing retention window, which is most of my reading
life, there is nothing. No duration data exists anywhere. No combination of proxies
recovers it. The layers I was going to stack don't exist.

## The column that fixed it

The obvious move is to build the estimator anyway and enjoy the dashboard. I nearly did.

What stopped me was imagining the dashboard in two years: a lifetime-hours figure sitting
at the top of the page, and no memory of which fraction of it I made up. A number like
that doesn't announce its own provenance. It just sits there looking authoritative, and
every month it gets harder to question because it's been true for longer.

So the session table has three extra columns:

```sql
source      TEXT NOT NULL,  -- koreader | kavita | playbooks | wellbeing
confidence  TEXT NOT NULL,  -- exact | approx | inferred
method      TEXT            -- which model produced an inferred row
```

Every aggregate query can filter to `confidence = 'exact'` and still mean something.
Modelled rows carry the name of the model that made them. When I look at a chart in
2028 and something seems off, I can ask which layer it came from and get an answer.

This is not a sophisticated idea. It's just the discipline of writing down that you
guessed, at the moment you guess, because you will not remember later and the guess will
not look like a guess by then.

## What I ended up with

Four routes on a Worker. A D1 database. An ingest pipeline on the laptop that normalizes
files before they're published and never touches them again — because the sync protocol
identifies books by content hash, so a file that changes becomes a different book. A
Kavita instance holding the library. KOReader on a Pixel and two tablets, each shipping
its per-page statistics database back to the laptop — that's where the `exact` rows
come from. And a `/reading` page fed by whatever I actually close.

And a reading history that starts, honestly, in the middle of 2026. Which stung for
about a day, and then stopped mattering. Six months of measured data will be worth more
than a decade of reconstruction, and it accumulates on its own.

<!-- TODO: replace with the real repository URL before publishing -->
The code is [on GitHub](https://github.com/) if any of it is useful to you. The part I'd
actually recommend stealing is the three columns.
