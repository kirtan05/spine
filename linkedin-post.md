# LinkedIn post

I spent a weekend building a reading tracker and learned something I didn't expect.

The build was straightforward. Reading position is a single number — it can't be merged,
only chosen — so it needs to live somewhere always reachable. Reading sessions are
append-only per device and merge trivially, so they can live anywhere and catch up later.

Most self-hosted setups put both on the same box as the library. That's backwards. The
library server is large and sleepy; the sync endpoint is four HTTP routes and a table. I
put the endpoint on a Cloudflare Worker attached to my personal site and left the library
on a laptop that's allowed to sleep.

Then I tried to reconstruct my reading history from download timestamps, app-usage data
and search history — and found that most of it simply isn't recoverable. Screen-time data
survives days to months depending on granularity — never years. Search history is noise.
The layers I planned to stack didn't exist.

So the schema now carries three columns on every row: `source`, `confidence`, and
`method`. Every aggregate can filter to measured-only. Estimated rows record which model
produced them.

That's the part I'd carry into real systems. Not the architecture — the habit of writing
down that you guessed, at the moment you guess. A number that mixes measurement with
estimation and doesn't say so is worse than no number, and it gets harder to question the
longer it sits there looking authoritative.

Write-up and code in the comments.

#SoftwareEngineering #DataEngineering #SelfHosting #Cloudflare
