# Joanna Garland tracker

A small website for Joanna Garland’s upcoming WTA and ITF entries, plus her singles results.

The public page refreshes once a day, around 6:00 in the morning Central European time. GitHub sometimes starts that job a little later.

On this computer you can still open a local copy:

```bash
python3 server.py
```

Then go to http://127.0.0.1:8765 and use Update. That check stays on your machine. The website picks up the new schedule on the next morning run.

Official pages remain the source of record:

- [WTA profile](https://www.wtatennis.com/players/326512/joanna-garland)
- [ITF profile](https://www.world.tennis/en/players/joanna-garland/800446517/tpe/wt/s/overview/)

ITF spots come from the public acceptance-list feed. Priority 1 is her first-choice tournament that week. WTA events show up once the tournament publishes her on the entry list.
