# The approach: security, crawler blocking, alerting

These skills grew out of running [allshorts.net](https://allshorts.net) with a very small team: no operations
staff, nobody watching dashboards, one chat that everything reports to. This page describes the approach we
settled on, in three parts. It is about method, not about our systems: there are no addresses, thresholds,
schedules or incident details here, on purpose. A description of how a site is defended should not double as a
map of it.

## Security

1. **Secrets live apart from everything readable.** Tokens are never in the repository or the main config. On a
   host they sit in one file only root can read; on Cloudflare they are Worker secrets. Anything that can be
   shown, shared or committed holds no secret.
2. **The monitor is not a new way in.** It opens no port and accepts no connections; it only makes outbound
   requests. It runs for a moment each minute and exits, under a restricted service unit, instead of being a
   daemon that stays up.
3. **Learn what is normal, then tell about what is new.** On its first run the monitor records a baseline: who
   logs in and from where, the files that grant access or persistence, which ports listen publicly, which
   containers exist. After that, anything outside the baseline is told once. A deploy or a new key will ping it;
   that is the point, and one message is a fair price.
4. **Tell, do not act.** The monitor reports and a person decides. An unexplained change is treated as an
   incident: look, do not clean up, decide together what to rotate or isolate.
5. **Something outside must be watching.** A host that is down, cut off or taken over cannot be trusted to report
   itself. Each host sends an authenticated heartbeat to a watcher that runs somewhere else, and silence becomes
   an alert.
6. **The watcher is watched too.** Hosts check the watcher back, and a report arrives on a schedule, so a monitor
   that has quietly stopped is noticed by its absence.

## Crawler blocking

1. **Edge rules first, and do not stop there.** A bot challenge and per-address rate limits stop the crude
   crawlers. A patient one passes both: every single request looks like a person.
2. **Look at traffic as a whole, on a schedule.** What gives a machine away is the pattern over a window of time,
   not any one request. A scheduled job asks the CDN's analytics API about the recent past and looks for traffic
   that no person produces. This replaced someone looking at a dashboard now and then.
3. **Tell once, with the fix attached.** A finding goes to the chat one time, together with what to add to the
   firewall rule. A person applies the block, so a mistake cannot lock out real visitors or a search engine.
4. **Known good crawlers never count.** Verified search engine crawlers are excluded at the source, both from
   detection and from error reports.
5. **Put the real limit where the rule lives.** What a network rule cannot see, the application can: it knows
   accounts and what a normal visit looks like. The lasting fixes were limits in the application itself; the
   watching is what showed where they were needed.
6. **Keep the thresholds private and keep tuning them.** Published numbers are instructions for staying under
   them.

## Alerting

1. **One chat.** Errors, security and machine health arrive in one place, in one format. For a small team, a
   second place to look is a place nobody looks.
2. **Tell changes, not states.** One message when something breaks and one when it recovers, with how long it
   lasted. Nothing repeats every minute.
3. **Confirm before telling.** Several bad readings in a row, not one. A value must come back past its threshold
   by a margin before it counts as recovered, or it flaps.
4. **Group.** Everything that changed in one run is one message; many events of the same kind are one line with a
   count.
5. **Work in progress is not an error.** Elapsed time alone never makes something a failure. Recheck before
   telling, and drop what has since succeeded.
6. **Urgent things go at once; the rest waits for the digest.** Few things are urgent. Decide which, explicitly.
7. **A failed send must not lose the alert.** Mark it sent only after the chat service accepted it, and retry on
   the next run. If the formatting is refused, send plain text: a plain alert beats none.
8. **Repeat only what is critical, and rarely.**
9. **A silence that expires and still watches.** During maintenance the checks keep running; when the silence
   ends, whatever is still wrong is reported.
10. **A report on a schedule is a heartbeat.** If the daily message did not come, the monitor is what broke.
11. **Monitoring moves with what it watches.** A check for a service that has moved away alerts forever, and a
    permanent alert gets muted along with the real ones. Removing a check is part of moving a service.
12. **Verify by watching it fire.** Every setup ends with a test message and a deliberately caused alert. A
    schedule is deployed when you have seen it run, not when the code is merged.

One thing that cost us time: turning a Telegram group into a supergroup gives it a new chat id, and every send
fails until the new id is configured. The alert chat going quiet was the only symptom.

## What it has been good for

- A message in the chat means a person should do something. Every rule above exists to keep that true.
- Patterns that no per-request rule sees, slow or spread across many addresses, were found by looking at traffic
  in aggregate, and led to limits where they belong.
- Nobody has to remember to look. The system says when it needs attention, and says when it is itself unwell.

We do not publish counts of alerts, thresholds or incident details.

## What these skills carry over

The alerting rules, the security baseline that learns first and tells once, the outside watcher with heartbeats,
and the secret handling are all in the skills as defaults. The analytics-based crawler watch and
application-level error digests are not included: they depend on the application. The pattern is what is worth
copying. What we learned from other projects is in [prior-art.md](prior-art.md).
