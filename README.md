# Joanna Garland tracker

A small website for Joanna Garland’s upcoming WTA and ITF entries, plus her singles results.

The public page is [joannagarland.com](https://joannagarland.com). It refreshes once a day, around 6:00 in the morning Central European time. GitHub sometimes starts that job a little later.

On this computer you can still open a local copy:

```bash
python3 server.py
```

Then go to http://127.0.0.1:8765 and use Update. That check stays on your machine. The website picks up the new schedule on the next morning run.

Official pages remain the source of record:

- [WTA profile](https://www.wtatennis.com/players/326512/joanna-garland)
- [ITF profile](https://www.world.tennis/en/players/joanna-garland/800446517/tpe/wt/s/overview/)

ITF spots come from the public acceptance-list feed. Priority 1 is her first-choice tournament that week. WTA events show up once the tournament publishes her on the entry list.

The page can send two kinds of email: one the morning before a tournament starts (or that morning, if the day before was missed), and one for each new singles result. Someone has to confirm from their own inbox before they are added. Addresses stay with the email service and are not stored in this repository.
