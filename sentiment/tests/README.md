# Sentiment dashboard regression checks

Run with Node.js 18 or later from the repository root:

```sh
node --test sentiment/tests/dashboard.test.cjs
```

The dependency-free suite runs the page's actual inline application code in an isolated browser/Leaflet stub. It covers complete state/zone validation, escaped source text, failed and timed-out requests, geometry holes, preserved assessment dates, state panels, accessibility button state, and unavailable map libraries. The richer company dashboard additionally covers independent optional feeds, boundary-failure fallback, historical theme rendering and coalesced slider updates.

These are application regressions, not visual browser tests or live deployment verification. Production JSON and its dates are never rewritten by the tests. The public boundary dataset was separately checked against the validator on 25 October 2026 (37 matching features).
