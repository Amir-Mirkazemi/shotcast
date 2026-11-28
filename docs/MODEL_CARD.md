# Model card: shots-on-target forecast

## What it predicts
For a player in an upcoming match: the expected number of shots on target, the chance of at least one and at
least two, and (for fixed-schedule leagues) a range for his season total. It does not predict goals, results
or anything about teams.

## Intended use
A public demonstration of forecasting with honest validation. Not for betting, fantasy advice or scouting
decisions. The data is a free, unofficial feed and the model has no access to injuries, tactics or line-ups.

## Data
ESPN match summaries stored by the nightly backfill (see `README.md`, "Data quality and warehouse"). Each
training row is one player in one match with features known before kickoff. Rows come only from matches with a
stored lineup and minutes.

## Features (all computed from earlier matches only)
Shrunk shots-on-target rate over the last 10 matches and over his stored history, shrunk shots rate, average
minutes over the last 5 matches, number of recent appearances, home or away, the team's recent shot volume, the
opponent's recent shots conceded, share of recent matches started, days since his last match. A test checks that
changing a match's own result never changes that match's own features.

## Models compared
A baseline (own recent rate x expected minutes), a league average, a Poisson regression, scikit-learn
gradient boosting with Poisson loss and LightGBM with Poisson loss. The baseline is a real competitor, not a
straw man.

## Validation
Walk-forward: the newest 40% of days is cut into four consecutive test periods; each is predicted by a model
trained only on earlier matches. A model is used only if it wins in most periods **and** the 95% interval of
its gain over the baseline (resampling whole matches) stays above zero. Otherwise the baseline is used and the
site says so. Results, per-period numbers and calibration tables are published on the Forecast page and are
regenerated on every run.

## Uncertainty
Counts of shots on target vary more than a Poisson allows. The forecast is a negative binomial; its dispersion is
estimated by moments on out-of-sample predictions. Chance-of-1+ is checked in five groups against what happened
(reliability table) and with the Brier score. Season ranges combine uncertainty in the player's true rate with
match-to-match noise. In simulations with known truth the ranges covered 85-90% of outcomes for a nominal 80%
(slightly wide, never overconfident); the earlier plain-Poisson ranges covered only 62-87%.

## Known limits
- Simulations check the maths, not the real world. Real-data performance is whatever the live Forecast page
  shows; when the baseline wins, that is the result.
- A single match forecast cannot have a meaningful 80% range for counts of 0, 1 or 2, so none is shown; the
  chances of 1+ and 2+ are shown instead.
- Players who change club, return from injury or change role break the "recent form" assumption.
- Competitions without a fixed schedule have no season projection.
- ESPN's coordinate, minutes and substitution data have gaps; the Data health page lists them.

## Reproduce
`python -m shotcast.forecast` (needs the stored matches). `pytest tests/test_ml.py` runs the simulation tests.
