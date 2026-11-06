#!/usr/bin/env node
/**
 * Headline Forward P/E tooltip dates. No network.
 * fmtDate is taken from index.html so the test cannot drift from the page.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const htmlPath = path.join(__dirname, "..", "index.html");
const html = fs.readFileSync(htmlPath, "utf8");

function fail(msg) {
  console.error("FAIL: " + msg);
  process.exit(1);
}

const src = html.match(/function fmtDate\(iso\) \{[\s\S]*?\n  \}/);
if (!src) fail("fmtDate not found in index.html");
let fmtDate;
try {
  fmtDate = eval("(" + src[0] + ")");
} catch (e) {
  fail("fmtDate did not parse: " + e.message);
}

function eq(actual, expected, label) {
  if (actual !== expected) fail(label + ": " + JSON.stringify(actual) + " !== " + JSON.stringify(expected));
  if (/undefined|NaN/.test(String(actual))) fail(label + " rendered " + actual);
}

// Historic and latest observations, date-only, stay on that calendar day.
eq(fmtDate("1996-01-03"), "Jan 3, 1996", "historic");
eq(fmtDate("1990-06-29"), "Jun 29, 1990", "series start");
eq(fmtDate("2008-12-31"), "Dec 31, 2008", "year end");
eq(fmtDate("2024-02-29"), "Feb 29, 2024", "leap day");
eq(fmtDate("2026-11-05"), "Nov 5, 2026", "latest forward P/E point");
eq(fmtDate("  2026-11-05  "), "Nov 5, 2026", "padded iso");

// Date-only with a time suffix still uses the calendar date written in the string,
// including a Z or an offset that would otherwise move the local day.
eq(fmtDate("2026-11-05T00:00:00"), "Nov 5, 2026", "local midnight suffix");
eq(fmtDate("2026-11-05T00:00:00Z"), "Nov 5, 2026", "utc midnight suffix");
eq(fmtDate("2026-11-05T23:30:00-08:00"), "Nov 5, 2026", "offset suffix");

// The bug: Chart.js time scale hands the tooltip a preformatted label, and the
// old formatter appended "T00:00:00", producing an Invalid Date.
eq(fmtDate("Nov 5, 2026"), "Nov 5, 2026", "preformatted tooltip label");
eq(fmtDate("Jan 3, 1996"), "Jan 3, 1996", "preformatted historic label");

// A UTC-midnight timestamp (what a time scale stores for a date-only label)
// keeps the UTC calendar day in every timezone.
eq(fmtDate(Date.UTC(2026, 10, 5)), "Nov 5, 2026", "utc timestamp");
eq(fmtDate(Date.UTC(1996, 0, 3)), "Jan 3, 1996", "historic utc timestamp");

const local = new Date(2026, 10, 5);
eq(fmtDate(local), "Nov 5, 2026", "local Date object");

for (const bad of [null, undefined, "", "   ", "undefined", "NaN", "undefined NaN, NaN",
                   "not-a-date", "2026-13-01", "2026-00-10", "2026-11-00", "2026-11-32",
                   Number.NaN, Number.POSITIVE_INFINITY, new Date(NaN)]) {
  const out = fmtDate(bad);
  eq(out, "—", "invalid " + String(bad));
}

// The headline tooltip must format the observation date, not the scale label.
const title = html.match(/title:\s*\(items\)\s*=>\s*\{[\s\S]*?\n              \}/);
if (!title) fail("headline tooltip title callback not found");
if (!/metrics\.series\[item\.dataIndex\]/.test(title[0])) {
  fail("tooltip title does not use the series date at dataIndex");
}
if (/fmtDate\(\s*items\[0\]\.label\s*\)/.test(html)) {
  fail("tooltip still formats the time-scale display label");
}
if (!/Forward P\/E:/.test(html) || !/Forward earnings yield:/.test(html)) {
  fail("tooltip lost a value mode");
}
if (/undefined NaN/.test(html)) fail("page source still contains the broken tooltip text");

console.log("OK fmtDate tz=" + (process.env.TZ || "default") + " latest=Nov 5, 2026 historic=Jan 3, 1996");
