# Request draft: US production tenant data in Redash (2026-11-04)

Prepared for the owner to send; the assistant sends nothing. No key, token, tenant UUID or customer data is in it.
Fill in the names in angle brackets before sending.

**Read-only facts behind it (owner's signed-in Redash, 2026-11-04):**

- redash.pentera.io has 10 data sources. The only production tenant source is "Prod (Cloned) - Mgmt" (id 2). Queries 251 and 253 read it.
- No US data source exists. Searches for US, Prod US, USA, us.app, us-east and United States found no US production query.
- An existing query on id 2 is named "All Users - Prod EU" (89), so id 2 looks EU only.
- The clone data is refreshed once a day: today's newest snapshot (17:28Z) reports `data_latest_modified` 2026-11-04T00:02:02Z. Our hourly query re-reads cannot get anything newer.
- US tenants are missing from the clone: CO-0825 (UUID written by hand) and CO-0835 (Ion Bank, United States) are not in it.

---

## Who to ask (found 2026-11-04, read-only)

- **Omer Gafni: Redash admin (owner confirmed 2026-11-04).** He reset the owner's Redash user (2026-09-10) and is the one people ask to change Redash permissions (2025-08-18 request). Help Desk ticket #15519 said "Product manages Redash".
- **Surface Product:** Orel, co-owned with Tomer Yanovich (VP of Product), per Tomer's "Product Management Ownership & Direct Contacts" email (2026-10-15, forwarded by Bart Hammond 2026-10-27).
- **Or Smolnik:** wrote most Redash queries (75 on "Prod (Cloned) - Mgmt"), including "All Users - Prod EU", but is no longer with the company. Do not contact; his queries are reference only.
- The clone refresh schedule probably belongs to R&D/DevOps, not Redash. Omer can point to them.

## Slack version (short)

> Hi Omer (cc Orel, Tomer), a request for Redash.
>
> 1. Our Surface onboarding duplicate check reads production tenants from "Prod (Cloned) - Mgmt". That source looks EU only, so tenants on us.app.pentera.io are invisible and the check can't be trusted for US customers.
> 2. Is there a US production Mgmt database (a clone like the EU one) that Redash can read? If yes, could you add it as a data source, e.g. "US Prod (Cloned) - Mgmt"?
> 3. Once it exists, I'd create one new read-only saved query on it (same columns as query 253) and need its API key delivered to me through your secure channel, plus an hourly scheduled refresh.
> 4. The clone data itself is refreshed once a day (latest data around 00:00 UTC), so a tenant created during the day is invisible until the next night. Could the production clone (EU, and US once it exists) be refreshed 2-3 times a day, e.g. around 00:00, 08:00 and 14:00 UTC? If that is too heavy, twice a day would already help.
>
> If the US data lives in a separate Redash instance instead, please point me to it. Who should I talk to if it isn't you?

---

## Email version

**To:** Omer Gafni (Redash admin)
**Cc:** Orion Mondragon (team leader); John Ostrander (team); Orel <surname> and Tomer Yanovich (Surface Product owners)
**Subject:** Request: US production tenant data source in Redash (Surface onboarding duplicate check)

Hi Omer,

The Surface Customer Onboarding automation checks every new onboarding against our production tenant list before anything is
created, so we never create a second tenant for the same customer. It reads that list from Redash, saved query 253 on the data
source "Prod (Cloned) - Mgmt".

That data source appears to cover the EU environment (app.pentera.io) only. Tenants created on the US environment
(us.app.pentera.io) are not in it, so for US customers the check reports "no tenant found" even when one exists. We have
two current onboardings where this happened.

**What I am asking for**

1. **Confirm the scope:** is "Prod (Cloned) - Mgmt" EU only, and is there a US production Mgmt database (or a clone of it)?
2. **A US data source:** if it exists, add it to redash.pentera.io as a data source, e.g. "US Prod (Cloned) - Mgmt",
   read-only, the same way the EU clone is set up. If the US data is in a separate Redash instance, please tell me its address
   and how to request access.
3. **One saved query and its key:** I would create one new read-only saved query on it (same columns as query 253; I never
   edit existing queries). Please deliver that query's API key through your secure channel (not email, chat or ticket text)
   and set an hourly scheduled refresh on it.
4. **More frequent clone refresh:** the clone data behind "Prod (Cloned) - Mgmt" is refreshed once a day (today's latest data
   is from about 00:00 UTC), so a tenant created during the day is invisible until the next night, however often the query
   runs. Could the production clone (EU, and US once it exists) be refreshed 2-3 times a day, for example around 00:00, 08:00
   and 14:00 UTC? If three is too heavy, twice a day would already help. If the clone is owned by another team, please point
   me to them.

**How it is used (read-only)**

- Only GET the saved query's latest result (and ask Redash to re-run that same query when the result is older than an hour).
  Nothing is created, edited or deleted in Redash.
- Hourly. Personal data is removed before storing; logs hold only counts and reason codes, never rows or keys.
- The same collector will later run from the onboarding VM (workato-opa-01), together with the EU query, once its network
  path to redash.pentera.io is open (IT ticket 16160).

Thanks,
<your name>

---

## 2026-11-05: Omer's answer and what we found (verified read-only from Redash in the owner's Chrome)

**Omer (2026-11-05 01:26):** added "Prod US (Cloned) - Mgmt" and "Prod US (Cloned) - Attack" and gave the customer
solutions group Full Access. Items 3 (key via secure channel) and 4 (clone refresh 2-3 times a day) were not answered yet.

**Checks (2026-11-05 ~18:15Z, nothing saved in Redash):**

| Data source | Redash id | Collections (schema refresh) | `tenantsMView` rows | `accounts` rows |
|---|---|---|---|---|
| Prod (Cloned) - Mgmt (EU) | 2 | 100 | 5,469 | n/a |
| Prod US (Cloned) - Mgmt | 14 | **0** | **0** | **0** |
| Prod US (Cloned) - Attack | 13 | not checked | **0** | n/a |

- The US sources connect without an error but are empty: most likely the database name in the data source, or the US
  clone has not been loaded yet. A US saved query cannot be built until this is fixed.
- EU clone freshness: newest `_modified` 2026-11-05 00:00:00Z, newest `_created` 2026-11-03 21:36Z. Query 253 already has an
  hourly schedule (interval 3600) and its last result was 17:29Z, so the daily clone load is the only freshness limit.

### Reply to Omer (draft; the owner sends it, Reply all)

Subject: RE: Request: US production tenant data source in Redash (Surface onboarding duplicate check)

> Hi Omer,
>
> Thanks for adding them so fast. I can see both US sources, but they look empty:
>
> 1. **Prod US (Cloned) - Mgmt:** the schema refresh returns 0 collections, and `tenantsMView` and `accounts` return 0 rows.
>    The EU "Prod (Cloned) - Mgmt" returns 100 collections and 5,469 tenants with the same queries.
> 2. **Prod US (Cloned) - Attack:** also 0 rows.
>
> Could you check the database name on the two US data sources, or whether the US clone has been loaded yet?
>
> Two open items from my first mail:
>
> 1. **Clone refresh:** the EU clone still loads once a day (newest data 00:00 UTC). Can it run 2-3 times a day (e.g.
>    00:00, 08:00, 14:00 UTC), and the US one the same? If another team owns the clone job, who should I ask?
> 2. **Key:** once the US source has data I will create one read-only saved query on it (same columns as 253) and send you
>    its id. Please share its API key through your secure channel, not email or chat.
>
> Thanks,
> Milton
