"""
Chronos — Temporal Replay (Incident Dossier edition).

Generates a self-contained, offline-first HTML incident report.
Aesthetic: 1950s detective case file meets Doc Brown's chalkboard.
Fully committed BTTF theming (Time Circuits readout, flux watermark).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from chronos.investigator import Evidence
from chronos.llm import RootCauseReport


_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>Chronos · Incident Dossier</title>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,700;0,900;1,700&family=IBM+Plex+Mono:wght@300;400;500;700&family=Special+Elite&family=Cabin+Sketch:wght@700&display=swap" rel="stylesheet">
<style>
  :root {
    --paper: #f3e9d2;
    --paper-shadow: #e5d7b3;
    --ink: #1a1815;
    --ink-soft: #3d352c;
    --ink-faded: #6b5d4a;
    --red-ink: #a61d22;
    --red-ink-soft: #c54d4d;
    --brass: #9a7a3d;
    --brass-dark: #6b5325;
    --amber: #e8a328;
    --green-crt: #3fa14a;
    --blue-crt: #2b6cb0;
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0; padding: 0;
    font-family: 'IBM Plex Mono', ui-monospace, Menlo, monospace;
    color: var(--ink);
    background-color: var(--paper);
    background-image:
      /* subtle grain */
      radial-gradient(rgba(0,0,0,0.04) 1px, transparent 1px),
      /* warm paper wash */
      radial-gradient(ellipse at 10% 10%, rgba(166,29,34,0.05) 0%, transparent 50%),
      radial-gradient(ellipse at 90% 90%, rgba(105,82,45,0.06) 0%, transparent 50%);
    background-size: 3px 3px, 100% 100%, 100% 100%;
    min-height: 100vh;
    line-height: 1.5;
  }
  .container {
    max-width: 1180px;
    margin: 0 auto;
    padding: 40px 48px 80px;
    position: relative;
  }

  /* Corner CLASSIFIED stamp */
  .classified-stamp {
    position: absolute;
    top: 28px; right: 36px;
    color: var(--red-ink);
    border: 3px solid var(--red-ink);
    padding: 6px 14px 4px;
    font-family: 'Special Elite', 'IBM Plex Mono', monospace;
    font-size: 20px;
    letter-spacing: 0.12em;
    transform: rotate(6deg);
    opacity: 0.78;
    user-select: none;
    box-shadow: 1px 2px 0 rgba(166,29,34,0.2);
  }

  /* ------------------ Masthead ------------------ */
  header.masthead {
    border-top: 3px double var(--ink);
    border-bottom: 3px double var(--ink);
    padding: 16px 0 10px;
    margin-bottom: 28px;
    text-align: center;
    position: relative;
  }
  header.masthead .eyebrow {
    font-family: 'Special Elite', monospace;
    font-size: 11px; letter-spacing: 0.35em;
    color: var(--ink-faded);
    text-transform: uppercase;
    margin-bottom: 4px;
  }
  header.masthead h1 {
    font-family: 'Playfair Display', serif;
    font-weight: 900; font-style: italic;
    font-size: 46px;
    letter-spacing: -0.01em;
    margin: 0;
    color: var(--ink);
  }
  header.masthead .sub {
    font-family: 'Special Elite', monospace;
    font-size: 12px;
    margin-top: 8px;
    color: var(--ink-soft);
    display: flex; justify-content: center; gap: 32px;
    flex-wrap: wrap;
  }
  header.masthead .sub span { letter-spacing: 0.08em; }
  header.masthead .sub strong {
    font-weight: 500; color: var(--red-ink);
  }

  /* ------------------ Time Circuits ------------------ */
  section.time-circuits {
    margin: 28px 0 36px;
    padding: 18px 22px;
    background: #0a0a0a;
    border: 2px solid #2a2a2a;
    border-radius: 6px;
    box-shadow: inset 0 0 40px rgba(0,0,0,0.8), 0 4px 0 rgba(0,0,0,0.1);
    position: relative;
  }
  section.time-circuits::before {
    content: "TIME CIRCUITS";
    position: absolute;
    top: -11px; left: 20px;
    background: var(--paper);
    padding: 0 10px;
    font-family: 'Special Elite', monospace;
    font-size: 10px;
    letter-spacing: 0.25em;
    color: var(--ink-faded);
  }
  .time-row {
    display: grid;
    grid-template-columns: 210px 1fr;
    align-items: center;
    padding: 8px 0;
    border-bottom: 1px dashed #1a1a1a;
  }
  .time-row:last-child { border-bottom: none; }
  .time-label {
    font-family: 'Special Elite', monospace;
    font-size: 11px; letter-spacing: 0.2em;
    text-transform: uppercase;
  }
  .time-label.destination { color: var(--red-ink-soft); }
  .time-label.present { color: var(--green-crt); }
  .time-label.departed { color: var(--amber); }
  .time-value {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 28px;
    font-weight: 500;
    letter-spacing: 0.08em;
    text-align: right;
    text-shadow: 0 0 8px currentColor;
  }
  .time-value.destination { color: var(--red-ink-soft); }
  .time-value.present { color: var(--green-crt); }
  .time-value.departed { color: var(--amber); }

  .time-right {
    display: flex; flex-direction: column; align-items: flex-end;
    gap: 2px;
  }
  .time-delta {
    font-family: 'Special Elite', monospace;
    font-size: 10px; letter-spacing: 0.25em;
    text-transform: uppercase;
    opacity: 0.7;
  }
  .time-delta.destination { color: var(--red-ink-soft); }
  .time-delta.present { color: var(--green-crt); }
  .time-delta.departed { color: var(--amber); }

  /* ------------------ Verdict ------------------ */
  section.verdict {
    padding: 28px 32px;
    background: var(--paper);
    border: 1px solid var(--ink);
    box-shadow: 6px 6px 0 var(--paper-shadow);
    margin-bottom: 36px;
    position: relative;
  }
  section.verdict .label {
    font-family: 'Special Elite', monospace;
    font-size: 11px; letter-spacing: 0.3em;
    text-transform: uppercase;
    color: var(--red-ink);
    margin-bottom: 10px;
  }
  section.verdict h2 {
    font-family: 'Playfair Display', serif;
    font-weight: 900;
    font-size: 30px;
    line-height: 1.25;
    margin: 0 0 18px;
    color: var(--ink);
  }
  section.verdict .underline {
    width: 100px; height: 3px;
    background: var(--red-ink);
    margin-bottom: 20px;
  }
  .verdict-grid {
    display: grid; grid-template-columns: repeat(4, 1fr);
    gap: 18px;
    margin-top: 8px;
  }
  .verdict-grid .cell {
    border-left: 2px solid var(--ink);
    padding-left: 12px;
  }
  .verdict-grid .cell .k {
    font-family: 'Special Elite', monospace;
    font-size: 10px; letter-spacing: 0.2em;
    text-transform: uppercase;
    color: var(--ink-faded);
    margin-bottom: 4px;
  }
  .verdict-grid .cell .v {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 15px; font-weight: 500;
    color: var(--ink);
  }
  .verdict-grid .cell .v.confidence {
    color: var(--red-ink); font-size: 20px;
  }

  /* Confidence stamp */
  .conf-stamp {
    position: absolute;
    top: 22px; right: 28px;
    border: 2.5px solid var(--red-ink);
    color: var(--red-ink);
    padding: 8px 16px 6px;
    font-family: 'Special Elite', monospace;
    font-size: 18px;
    letter-spacing: 0.1em;
    transform: rotate(-4deg);
    opacity: 0.88;
    text-align: center;
  }
  .conf-stamp .big { font-size: 24px; display: block; letter-spacing: 0.02em; }
  .conf-stamp .small {
    font-size: 9px; letter-spacing: 0.3em;
    margin-top: 2px;
  }

  /* ------------------ Explanation ------------------ */
  section.explanation {
    margin-bottom: 36px;
    padding: 26px 32px 22px;
    background: #fbf5e4;
    border: 1px solid var(--ink-faded);
    box-shadow: 3px 3px 0 var(--paper-shadow);
    position: relative;
  }
  section.explanation::before {
    content: "";
    position: absolute;
    top: 6px; left: 6px; right: 6px; bottom: 6px;
    border: 1px dashed var(--ink-faded);
    opacity: 0.35;
    pointer-events: none;
  }
  .explanation-head {
    font-family: 'Special Elite', monospace;
    font-size: 10px; letter-spacing: 0.3em;
    text-transform: uppercase;
    color: var(--red-ink);
    margin-bottom: 12px;
  }
  #explanation-body {
    margin: 0 0 16px;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 14.5px; line-height: 1.7;
    color: var(--ink);
  }
  .explanation-links {
    display: flex; align-items: center; gap: 14px;
    padding-top: 12px;
    border-top: 1px dashed var(--ink-faded);
    font-family: 'Special Elite', monospace;
    font-size: 10px; letter-spacing: 0.2em;
    text-transform: uppercase;
  }
  .explanation-links a {
    color: var(--red-ink);
    text-decoration: none;
    border-bottom: 1px dotted var(--red-ink);
    padding-bottom: 1px;
  }
  .explanation-links a:hover {
    background: rgba(166,29,34,0.08);
  }
  .explanation-links .spacer { color: var(--ink-faded); }
  .explanation-links .timing { color: var(--ink-faded); }

  /* ------------------ Timeline hero ------------------ */
  section.timeline {
    position: relative;
    padding: 40px 20px 60px;
    margin-bottom: 36px;
    background:
      repeating-linear-gradient(
        to right,
        transparent 0 38px,
        rgba(0,0,0,0.04) 38px 39px
      ),
      repeating-linear-gradient(
        to bottom,
        transparent 0 38px,
        rgba(0,0,0,0.04) 38px 39px
      ),
      var(--paper);
    border: 1px solid var(--ink-faded);
    overflow: hidden;
  }
  /* Flux capacitor watermark */
  section.timeline::before {
    content: "";
    position: absolute;
    top: 50%; left: 50%;
    width: 340px; height: 340px;
    transform: translate(-50%, -50%);
    background:
      radial-gradient(circle at center, rgba(166,29,34,0.08) 0%, transparent 70%);
    pointer-events: none;
  }
  section.timeline::after {
    content: "";
    position: absolute;
    top: 50%; left: 50%;
    width: 240px; height: 240px;
    transform: translate(-50%, -50%);
    background-image:
      linear-gradient(60deg, transparent 48%, rgba(166,29,34,0.22) 48% 52%, transparent 52%),
      linear-gradient(180deg, transparent 48%, rgba(166,29,34,0.22) 48% 52%, transparent 52%),
      linear-gradient(300deg, transparent 48%, rgba(166,29,34,0.22) 48% 52%, transparent 52%);
    pointer-events: none;
    opacity: 0.35;
  }
  .timeline-header {
    display: flex; justify-content: space-between; align-items: baseline;
    margin-bottom: 28px;
    position: relative; z-index: 2;
  }
  .timeline-header h3 {
    font-family: 'Playfair Display', serif;
    font-weight: 700; font-style: italic;
    font-size: 22px;
    margin: 0;
  }
  .timeline-header .meta {
    font-family: 'Special Elite', monospace;
    font-size: 11px; letter-spacing: 0.2em;
    text-transform: uppercase;
    color: var(--ink-faded);
  }

  .track-wrap {
    position: relative; z-index: 2;
    padding: 60px 0 80px;
  }
  .track {
    position: relative;
    height: 4px;
    background: var(--ink);
    margin: 0 20px;
  }
  .track-tick {
    position: absolute; top: -6px;
    width: 2px; height: 14px;
    background: var(--ink-faded);
  }
  .failure-marker {
    position: absolute; top: -68px; bottom: -68px;
    width: 2px;
    background: var(--red-ink);
    opacity: 0.7;
    z-index: 3;
  }
  .failure-marker::before {
    content: "⚡ FAILURE FIRED";
    position: absolute;
    top: -18px; left: 50%;
    transform: translateX(-50%);
    font-family: 'Special Elite', monospace;
    font-size: 10px;
    letter-spacing: 0.25em;
    color: var(--red-ink);
    white-space: nowrap;
  }
  .scrub-marker {
    position: absolute; top: -68px; bottom: -68px;
    width: 2px;
    background: var(--brass);
    z-index: 4;
    box-shadow: 0 0 4px var(--brass);
  }
  .scrub-marker::after {
    content: "";
    position: absolute;
    bottom: -6px; left: 50%;
    transform: translateX(-50%);
    width: 0; height: 0;
    border-left: 6px solid transparent;
    border-right: 6px solid transparent;
    border-top: 8px solid var(--brass);
  }

  .event-card {
    position: absolute;
    transform: translate(-50%, 0);
    min-width: 80px; max-width: 150px;
    padding: 6px 10px;
    background: var(--paper);
    border: 1px solid var(--ink);
    box-shadow: 2px 2px 0 var(--paper-shadow);
    cursor: pointer;
    transition: transform 0.2s, box-shadow 0.2s;
    z-index: 5;
  }
  .event-card:hover {
    transform: translate(-50%, -3px);
    box-shadow: 3px 4px 0 var(--paper-shadow);
    z-index: 10;
  }
  .event-card.above { top: -56px; }
  .event-card.below { top: 26px; }
  .event-card .name {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 11px; font-weight: 500;
    color: var(--ink);
    margin-bottom: 2px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .event-card .ver {
    font-family: 'Special Elite', monospace;
    font-size: 9px; letter-spacing: 0.15em;
    color: var(--ink-faded);
  }
  .event-card .cat {
    font-family: 'Special Elite', monospace;
    font-size: 8px; letter-spacing: 0.18em;
    text-transform: uppercase;
    margin-top: 2px;
  }
  .event-card.schema-change {
    border-color: var(--red-ink);
    border-width: 2px;
  }
  .event-card.schema-change .cat { color: var(--red-ink); }
  .event-card.tier-change {
    border-color: var(--brass-dark);
  }
  .event-card.tier-change .cat { color: var(--brass-dark); }
  .event-card.cosmetic { opacity: 0.55; }
  .event-card.cosmetic .cat { color: var(--ink-faded); }

  .event-card.primary-suspect-card {
    background: #fff5f5;
    border: 2.5px solid var(--red-ink);
    box-shadow: 3px 3px 0 rgba(166,29,34,0.25);
    z-index: 7;
  }
  .event-card.primary-suspect-card .name {
    color: var(--red-ink);
    font-weight: 700;
  }

  .event-connector {
    position: absolute;
    width: 1px;
    background: var(--ink-faded);
    z-index: 4;
  }

  /* Primary suspect annotation */
  .suspect-annotation {
    position: absolute;
    top: -128px;
    transform: translateX(-50%);
    z-index: 6;
    pointer-events: none;
  }
  .suspect-annotation .label {
    font-family: 'Cabin Sketch', 'Special Elite', cursive;
    font-size: 26px;
    color: var(--red-ink);
    font-weight: 700;
    transform: rotate(-4deg);
    text-shadow: 1px 1px 0 rgba(166,29,34,0.18);
    white-space: nowrap;
  }
  .suspect-annotation .arrow {
    display: block;
    margin: 4px auto 0;
    width: 2px;
    height: 46px;
    background:
      linear-gradient(to bottom, var(--red-ink) 90%, transparent 90%);
    position: relative;
  }
  .suspect-annotation .arrow::after {
    content: "";
    position: absolute;
    left: 50%; bottom: 0;
    transform: translateX(-50%);
    width: 0; height: 0;
    border-left: 6px solid transparent;
    border-right: 6px solid transparent;
    border-top: 10px solid var(--red-ink);
  }

  /* ------------------ Scrubber ------------------ */
  section.scrubber-sec {
    margin-bottom: 36px;
    padding: 18px 24px;
    background: var(--paper);
    border: 1px solid var(--ink-faded);
    border-top: 3px solid var(--brass);
    display: flex; align-items: center; gap: 20px;
  }
  .scrubber-meta {
    min-width: 220px;
  }
  .scrubber-meta .label {
    font-family: 'Special Elite', monospace;
    font-size: 10px; letter-spacing: 0.25em;
    text-transform: uppercase;
    color: var(--ink-faded);
    margin-bottom: 2px;
  }
  .scrubber-meta .val {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 14px; color: var(--ink);
  }
  .scrubber-wrap { flex: 1; }
  input#scrubber {
    -webkit-appearance: none; appearance: none;
    width: 100%;
    background: transparent;
    height: 22px;
    cursor: grab;
  }
  input#scrubber::-webkit-slider-runnable-track {
    height: 4px;
    background: var(--ink);
    border-radius: 1px;
  }
  input#scrubber::-moz-range-track {
    height: 4px;
    background: var(--ink);
    border-radius: 1px;
  }
  input#scrubber::-webkit-slider-thumb {
    -webkit-appearance: none; appearance: none;
    width: 18px; height: 18px;
    margin-top: -7px;
    border-radius: 50%;
    background: radial-gradient(circle at 35% 35%, var(--amber), var(--brass-dark));
    border: 2px solid var(--ink);
    box-shadow: 0 2px 3px rgba(0,0,0,0.3);
    cursor: grab;
  }
  input#scrubber::-moz-range-thumb {
    width: 18px; height: 18px;
    border-radius: 50%;
    background: radial-gradient(circle at 35% 35%, var(--amber), var(--brass-dark));
    border: 2px solid var(--ink);
    cursor: grab;
  }

  /* ------------------ Detail panel ------------------ */
  section.detail {
    padding: 24px 28px;
    background: var(--paper);
    border: 1px solid var(--ink);
    box-shadow: 4px 4px 0 var(--paper-shadow);
    margin-bottom: 36px;
  }
  .detail-head {
    display: flex; justify-content: space-between;
    align-items: baseline;
    border-bottom: 1px dashed var(--ink-faded);
    padding-bottom: 10px; margin-bottom: 14px;
  }
  .detail-head .title {
    font-family: 'Playfair Display', serif;
    font-weight: 700;
    font-size: 18px;
    color: var(--ink);
  }
  .detail-head .category {
    font-family: 'Special Elite', monospace;
    font-size: 10px; letter-spacing: 0.25em;
    text-transform: uppercase;
    padding: 3px 10px;
    border: 1px solid var(--ink);
  }
  .detail-row {
    display: grid;
    grid-template-columns: 160px 1fr;
    padding: 6px 0;
    border-bottom: 1px dotted rgba(0,0,0,0.15);
    font-size: 13px;
  }
  .detail-row:last-child { border-bottom: none; }
  .detail-row .k {
    font-family: 'Special Elite', monospace;
    font-size: 10px; letter-spacing: 0.2em;
    text-transform: uppercase;
    color: var(--ink-faded);
    padding-top: 2px;
  }
  .detail-row .v {
    font-family: 'IBM Plex Mono', monospace;
    color: var(--ink); word-break: break-word;
  }
  .detail-diff {
    margin-top: 14px;
    padding: 12px 14px;
    background: rgba(166,29,34,0.06);
    border-left: 3px solid var(--red-ink);
    font-family: 'IBM Plex Mono', monospace;
    font-size: 12.5px; line-height: 1.5;
    color: var(--ink);
  }

  /* ------------------ Dossier cards ------------------ */
  .dossier-grid {
    display: grid; grid-template-columns: 1fr 1fr; gap: 22px;
    margin-bottom: 36px;
  }
  .dossier {
    padding: 22px 24px;
    background: var(--paper);
    border: 1px solid var(--ink);
    box-shadow: 4px 4px 0 var(--paper-shadow);
    position: relative;
  }
  .dossier h4 {
    font-family: 'Playfair Display', serif;
    font-style: italic;
    font-weight: 700;
    font-size: 16px;
    margin: 0 0 10px;
    padding-bottom: 8px;
    border-bottom: 2px solid var(--ink);
  }
  .dossier h4 .num {
    font-family: 'Special Elite', monospace;
    font-style: normal;
    font-size: 10px;
    letter-spacing: 0.25em;
    color: var(--red-ink);
    margin-right: 10px;
  }
  .dossier p {
    margin: 0;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 13px; line-height: 1.55;
    color: var(--ink);
  }
  .dossier ol, .dossier ul {
    margin: 0; padding-left: 0; list-style: none;
  }
  .dossier ol li, .dossier ul li {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 12.5px; line-height: 1.55;
    padding: 6px 0 6px 42px;
    border-bottom: 1px dotted rgba(0,0,0,0.1);
    position: relative;
  }
  .dossier ol li:last-child, .dossier ul li:last-child { border-bottom: none; }
  .dossier ol li .ev {
    position: absolute; left: 0; top: 6px;
    font-family: 'Special Elite', monospace;
    font-size: 11px;
    color: var(--red-ink);
    letter-spacing: 0.1em;
  }
  .dossier .paperclip {
    position: absolute;
    top: -12px; right: 18px;
    width: 28px; height: 48px;
    border: 2px solid var(--brass-dark);
    border-bottom-left-radius: 14px;
    border-bottom-right-radius: 14px;
    border-top: none;
    transform: rotate(12deg);
    opacity: 0.7;
  }

  .axis-labels {
    position: relative;
    height: 24px;
    margin-top: 20px;
  }
  .axis-labels .axis-lbl {
    position: absolute;
    transform: translateX(-50%);
    font-family: 'Special Elite', monospace;
    font-size: 10px; letter-spacing: 0.2em;
    color: var(--ink-faded);
    text-transform: uppercase;
  }
  .axis-labels .axis-lbl.failure { color: var(--red-ink); font-weight: 700; }

  /* ------------------ Footer ------------------ */
  footer.dossier-foot {
    margin-top: 36px; padding: 20px 0 10px;
    border-top: 3px double var(--ink);
    display: flex; justify-content: space-between;
    align-items: center; gap: 20px;
    font-family: 'Special Elite', monospace;
    font-size: 11px; letter-spacing: 0.2em;
    color: var(--ink-soft);
    flex-wrap: wrap;
  }
  footer.dossier-foot .signoff {
    font-family: 'Cabin Sketch', cursive;
    font-size: 26px; color: var(--red-ink);
    letter-spacing: 0.02em;
    transform: rotate(-2deg);
    text-transform: none;
  }

  @media (max-width: 900px) {
    .container { padding: 24px 20px 60px; }
    .classified-stamp { top: 14px; right: 16px; font-size: 15px; }
    header.masthead h1 { font-size: 32px; }
    .verdict-grid { grid-template-columns: repeat(2, 1fr); }
    .dossier-grid { grid-template-columns: 1fr; }
    .time-row { grid-template-columns: 140px 1fr; }
    .time-value { font-size: 19px; }
  }
</style>
</head>
<body>
<div class="container">

  <div class="classified-stamp">CLASSIFIED · CHRONOS</div>

  <header class="masthead">
    <div class="eyebrow">CHRONOS · Flux Operations Bureau</div>
    <h1>Incident Dossier</h1>
    <div class="sub">
      <span>CASE NO. <strong id="case-no">—</strong></span>
      <span>FILED <strong id="filed-date">—</strong></span>
      <span>STATUS <strong>ACTIVE</strong></span>
    </div>
  </header>

  <section class="time-circuits">
    <div class="time-row">
      <div class="time-label destination">DESTINATION TIME</div>
      <div class="time-right">
        <div class="time-value destination" id="destination-time">—</div>
        <div class="time-delta destination" id="destination-delta">—</div>
      </div>
    </div>
    <div class="time-row">
      <div class="time-label present">PRESENT TIME</div>
      <div class="time-right">
        <div class="time-value present" id="present-time">—</div>
        <div class="time-delta present" id="present-delta">—</div>
      </div>
    </div>
    <div class="time-row">
      <div class="time-label departed">LAST TIME DEPARTED</div>
      <div class="time-right">
        <div class="time-value departed" id="departed-time">—</div>
        <div class="time-delta departed" id="departed-delta">—</div>
      </div>
    </div>
  </section>

  <section class="verdict">
    <div class="label">VERDICT · ROOT CAUSE IDENTIFIED</div>
    <h2 id="verdict">—</h2>
    <div class="underline"></div>
    <div class="verdict-grid">
      <div class="cell">
        <div class="k">Affected Asset</div>
        <div class="v" id="affected">—</div>
      </div>
      <div class="cell">
        <div class="k">Column</div>
        <div class="v" id="column">—</div>
      </div>
      <div class="cell">
        <div class="k">Tier</div>
        <div class="v" id="tier">—</div>
      </div>
      <div class="cell">
        <div class="k">Fired</div>
        <div class="v" id="fired">—</div>
      </div>
    </div>
    <div class="conf-stamp">
      <span class="big" id="confidence-big">—</span>
      <span class="small">CONFIDENCE</span>
    </div>
  </section>

  <section class="explanation">
    <div class="explanation-head">INVESTIGATOR'S FINDINGS</div>
    <p id="explanation-body">—</p>
    <div class="explanation-links">
      <a id="om-link" href="#" target="_blank" rel="noopener">↗ VIEW IN OPENMETADATA</a>
      <span class="spacer">·</span>
      <span class="timing" id="timing">—</span>
    </div>
  </section>

  <section class="timeline">
    <div class="timeline-header">
      <h3>Chronology of Suspect Events</h3>
      <div class="meta">Metadata Log · Filtered to Suspect Window</div>
    </div>
    <div class="track-wrap">
      <div class="track" id="track">
        <div class="failure-marker" id="failure-marker"></div>
        <div class="scrub-marker" id="scrub-marker"></div>
      </div>
      <div class="axis-labels" id="axis-labels"></div>
    </div>
  </section>

  <section class="scrubber-sec">
    <div class="scrubber-meta">
      <div class="label">Flux Scrubber Position</div>
      <div class="val"><span id="scrub-time">—</span> · <span id="scrub-ago">—</span></div>
    </div>
    <div class="scrubber-wrap">
      <input type="range" id="scrubber" min="0" max="1000" value="1000"/>
    </div>
  </section>

  <section class="detail">
    <div class="detail-head">
      <div class="title" id="detail-title">Drag the flux scrubber to inspect events</div>
      <div class="category" id="detail-category">—</div>
    </div>
    <div id="detail-body">
      <p style="color: var(--ink-faded); font-size: 13px;">
        Each event dot is a version recorded in OpenMetadata's change log.
        The red annotation marks Chronos's primary suspect — the event most likely to have caused the failure.
      </p>
    </div>
  </section>

  <div class="dossier-grid">
    <div class="dossier">
      <div class="paperclip"></div>
      <h4><span class="num">§ 01</span>Blast Radius</h4>
      <p id="blast-radius">—</p>
    </div>
    <div class="dossier">
      <h4><span class="num">§ 02</span>Recommended Action</h4>
      <p id="suggested-fix">—</p>
    </div>
    <div class="dossier">
      <div class="paperclip"></div>
      <h4><span class="num">§ 03</span>Evidence Markers</h4>
      <ol id="evidence-chain"></ol>
    </div>
    <div class="dossier">
      <h4><span class="num">§ 04</span>Downstream Dependents</h4>
      <ul id="downstream"></ul>
    </div>
  </div>

  <footer class="dossier-foot">
    <div>FILED BY CHRONOS INVESTIGATION AGENT · CASE <span id="foot-case">—</span></div>
    <div class="signoff">— Great Scott! —</div>
    <div>Flux Capacitor Engaged</div>
  </footer>

</div>

<script>
const PAYLOAD = __PAYLOAD__;

(function init() {
  // Case metadata
  const caseNo = computeCaseNo(PAYLOAD);
  document.getElementById('case-no').textContent = caseNo;
  document.getElementById('foot-case').textContent = caseNo;
  document.getElementById('filed-date').textContent =
    formatDateLong(new Date(PAYLOAD.generated_at || Date.now()));

  // Verdict panel
  document.getElementById('verdict').textContent = PAYLOAD.report.verdict;
  document.getElementById('confidence-big').textContent = PAYLOAD.report.confidence + '%';
  document.getElementById('affected').textContent = PAYLOAD.affected.name;
  document.getElementById('column').textContent = PAYLOAD.affected.column || '—';
  document.getElementById('tier').textContent = PAYLOAD.affected.tier || '—';
  document.getElementById('fired').textContent = PAYLOAD.failure.hours_ago + 'h ago';

  // Explanation + links
  document.getElementById('explanation-body').textContent =
    PAYLOAD.report.explanation || '—';

  // OpenMetadata link
  const omBase = PAYLOAD.om_base_url || 'http://localhost:8585';
  const omLink = document.getElementById('om-link');
  omLink.href = `${omBase}/table/${encodeURIComponent(PAYLOAD.affected.fqn)}`;

  // Timing readout
  const t = PAYLOAD.timings || {};
  if (t.total != null) {
    document.getElementById('timing').textContent =
      `Investigation completed in ${t.total}s  ·  Evidence ${t.gather}s  ·  AI ${t.llm}s`;
  }

  // Bottom dossier
  document.getElementById('blast-radius').textContent = PAYLOAD.report.blast_radius;
  document.getElementById('suggested-fix').textContent = PAYLOAD.report.suggested_fix;

  const evList = document.getElementById('evidence-chain');
  (PAYLOAD.report.evidence_chain || []).forEach((e, i) => {
    const li = document.createElement('li');
    const tag = document.createElement('span');
    tag.className = 'ev';
    tag.textContent = 'EV-' + String(i + 1).padStart(2, '0');
    li.appendChild(tag);
    li.appendChild(document.createTextNode(e));
    evList.appendChild(li);
  });

  const dsList = document.getElementById('downstream');
  if (!PAYLOAD.downstream.length) {
    const li = document.createElement('li');
    li.style.color = 'var(--ink-faded)';
    li.textContent = 'None observed. Failure is confined to the affected table.';
    dsList.appendChild(li);
  } else {
    PAYLOAD.downstream.forEach(d => {
      const li = document.createElement('li');
      const tier = d.tier ? ' · ' + d.tier : '';
      li.textContent = `${d.name} (depth ${d.depth}${tier})`;
      dsList.appendChild(li);
    });
  }

  // --- Time Circuits ---
  const suspect = (PAYLOAD.report.primary_suspect || {});
  const failureTs = PAYLOAD.failure.ts_ms;
  const suspectTs = findEventTs(PAYLOAD.events, suspect.table_name, suspect.version);
  const departedTs = findLastEventBefore(PAYLOAD.events, suspectTs) || suspectTs;

  document.getElementById('destination-time').textContent = formatTimeCircuit(suspectTs || failureTs);
  document.getElementById('present-time').textContent = formatTimeCircuit(failureTs);
  document.getElementById('departed-time').textContent = formatTimeCircuit(departedTs);

  // Relative deltas
  function fmtDelta(ms, refMs, label) {
    if (!ms || !refMs) return '—';
    const diffMin = Math.round((ms - refMs) / 60000);
    const abs = Math.abs(diffMin);
    if (abs < 1) return 'MOMENT OF FAILURE';
    if (abs < 60) return `${abs} MIN ${diffMin < 0 ? 'BEFORE' : 'AFTER'} ${label}`;
    const hours = (abs / 60).toFixed(1);
    return `${hours}h ${diffMin < 0 ? 'BEFORE' : 'AFTER'} ${label}`;
  }
  document.getElementById('destination-delta').textContent =
    fmtDelta(suspectTs, failureTs, 'FAILURE');
  document.getElementById('present-delta').textContent = 'FAILURE FIRED HERE';
  document.getElementById('departed-delta').textContent =
    fmtDelta(departedTs, suspectTs, 'ROOT CAUSE');

  // --- Timeline ---
  renderTimeline(PAYLOAD, suspect);

  // --- Scrubber ---
  setupScrubber(PAYLOAD, suspect);
})();

function computeCaseNo(payload) {
  // Deterministic 6-digit case # from the test FQN
  let h = 0;
  const src = (payload.affected.fqn || '') + (payload.failure.test_name || '');
  for (let i = 0; i < src.length; i++) {
    h = ((h << 5) - h) + src.charCodeAt(i);
    h |= 0;
  }
  return 'CHR-' + String(Math.abs(h) % 100000).padStart(5, '0');
}

function formatDateLong(d) {
  const m = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
  return `${String(d.getUTCDate()).padStart(2,'0')} ${m[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

function formatTimeCircuit(ts) {
  if (!ts) return '—';
  const d = new Date(ts);
  const m = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
  const pad = n => String(n).padStart(2, '0');
  return `${m[d.getUTCMonth()]} ${pad(d.getUTCDate())} ${d.getUTCFullYear()}  ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
}

function findEventTs(events, tableName, version) {
  const e = (events || []).find(
    ev => ev.table_name === tableName && String(ev.version) === String(version)
  );
  return e ? e.ts_ms : null;
}

function findLastEventBefore(events, ts) {
  if (!ts) return null;
  const earlier = (events || []).filter(e => e.ts_ms < ts).sort((a,b) => b.ts_ms - a.ts_ms);
  return earlier.length ? earlier[0].ts_ms : null;
}

function classifyEvent(ev) {
  const s = (ev.change_summary || '').toLowerCase();
  if (s.includes('renamed') || s.includes('breaking change') || s.includes('column')) return 'schema-change';
  if (s.includes('tier') || s.includes('tags')) return 'tier-change';
  if (s.includes('version bump')) return 'cosmetic';
  return '';
}

function renderTimeline(payload, suspect) {
  const events = payload.events || [];
  if (!events.length) return;
  const track = document.getElementById('track');
  const failureTs = payload.failure.ts_ms;

  // --- Rank events by priority (most important = highest value) ---
  function priority(ev) {
    const cls = classifyEvent(ev);
    const isSuspect = ev.table_name === suspect.table_name
      && String(ev.version) === String(suspect.version);
    if (isSuspect) return 100;
    if (cls === 'schema-change') return 80;
    if (cls === 'tier-change') return 60;
    if (cls === 'cosmetic') return 20;
    return 40;
  }

  // --- Sort ascending by timestamp; within same-ish time, prioritized items go to distinct tracks ---
  const sorted = [...events].sort((a, b) => a.ts_ms - b.ts_ms);

  // Build time span. If all events are within a tiny window, pad it out so spreading has room.
  const evMin = Math.min(...sorted.map(e => e.ts_ms));
  const evMax = Math.max(...sorted.map(e => e.ts_ms));
  const evSpan = evMax - evMin;

  // Pad the window to at least 2× its own length, and always include the failure moment
  const padding = Math.max(evSpan * 0.5, 1);
  const minTs = Math.min(evMin - padding, failureTs - padding);
  const maxTs = Math.max(evMax + padding, failureTs + padding);
  const span = Math.max(maxTs - minTs, 1);
  const pct = ts => ((ts - minTs) / span) * 100;

  document.getElementById('failure-marker').style.left = pct(failureTs) + '%';

  // --- Evenly redistribute tightly-clustered events along X ---
  // If events are closer than minXGap percent, space them out while preserving their order
  const minXGap = 11;  // percent of timeline width minimum between cards
  const xPositions = sorted.map(e => pct(e.ts_ms));
  for (let i = 1; i < xPositions.length; i++) {
    if (xPositions[i] - xPositions[i - 1] < minXGap) {
      xPositions[i] = xPositions[i - 1] + minXGap;
    }
  }
  // Shift everything left if we ran past 95%
  const overshoot = Math.max(0, xPositions[xPositions.length - 1] - 95);
  if (overshoot > 0) {
    for (let i = 0; i < xPositions.length; i++) {
      xPositions[i] -= overshoot;
    }
  }

  // --- Render cards on 2 rows with alternation, but group same-table together ---
  sorted.forEach((ev, idx) => {
    const card = document.createElement('div');
    const cls = classifyEvent(ev);
    card.className = 'event-card ' + cls;

    const isSuspect = ev.table_name === suspect.table_name
      && String(ev.version) === String(suspect.version);

    const above = idx % 2 === 0;
    card.classList.add(above ? 'above' : 'below');
    if (isSuspect) card.classList.add('primary-suspect-card');
    card.style.left = xPositions[idx] + '%';

    const name = document.createElement('div');
    name.className = 'name'; name.textContent = ev.table_name;
    const ver = document.createElement('div');
    ver.className = 'ver'; ver.textContent = 'v' + ev.version;
    const cat = document.createElement('div');
    cat.className = 'cat';
    cat.textContent = (cls || 'event').replace('-', ' ');

    card.appendChild(name);
    card.appendChild(ver);
    card.appendChild(cat);
    card.addEventListener('click', () => showDetail(ev));
    track.appendChild(card);

    // Connector line from card to axis
    const conn = document.createElement('div');
    conn.className = 'event-connector';
    conn.style.left = xPositions[idx] + '%';
    if (above) {
      conn.style.top = '-22px';
      conn.style.height = '22px';
    } else {
      conn.style.top = '4px';
      conn.style.height = '22px';
    }
    track.appendChild(conn);

    // Suspect annotation
    if (isSuspect) {
      const ann = document.createElement('div');
      ann.className = 'suspect-annotation';
      ann.style.left = xPositions[idx] + '%';
      const lbl = document.createElement('div');
      lbl.className = 'label';
      lbl.textContent = 'ROOT CAUSE ↓';
      const arr = document.createElement('div');
      arr.className = 'arrow';
      ann.appendChild(lbl);
      ann.appendChild(arr);
      track.appendChild(ann);
    }
  });

  // Timeline axis ticks (7 evenly spaced)
  for (let i = 0; i <= 6; i++) {
    const tick = document.createElement('div');
    tick.className = 'track-tick';
    tick.style.left = (i * (100/6)) + '%';
    track.appendChild(tick);
  }

  // Expose for scrubber calculations
  renderTimeline._minTs = minTs;
  renderTimeline._maxTs = maxTs;
  renderTimeline._span = span;


  // Axis labels — 5 evenly spaced time markers
  const axisLabels = document.getElementById('axis-labels');
  axisLabels.innerHTML = '';
  const nowMs = Date.now();
  for (let i = 0; i <= 4; i++) {
    const pctVal = i / 4;
    const ts = minTs + pctVal * span;
    const lbl = document.createElement('div');
    const diffMin = Math.round((nowMs - ts) / 60000);
    const absMin = Math.abs(diffMin);
    let txt;
    if (absMin < 1) txt = 'NOW';
    else if (absMin < 60) txt = `${diffMin < 0 ? '+' : '−'}${absMin}m`;
    else txt = `${diffMin < 0 ? '+' : '−'}${(absMin/60).toFixed(1)}h`;
    lbl.className = 'axis-lbl';
    lbl.style.left = (pctVal * 100) + '%';
    lbl.textContent = txt;
    axisLabels.appendChild(lbl);
  }
  // Failure label
  const failLbl = document.createElement('div');
  failLbl.className = 'axis-lbl failure';
  failLbl.style.left = pct(failureTs) + '%';
  failLbl.textContent = '⚡ FAILURE';
  axisLabels.appendChild(failLbl);
}

function setupScrubber(payload, suspect) {
  const events = payload.events || [];
  if (!events.length) return;

  // Use the padded range computed by renderTimeline
  const minTs = renderTimeline._minTs;
  const maxTs = renderTimeline._maxTs;
  const span = renderTimeline._span;

  
  const scrubber = document.getElementById('scrubber');
  const scrubMarker = document.getElementById('scrub-marker');
  const scrubTime = document.getElementById('scrub-time');
  const scrubAgo = document.getElementById('scrub-ago');

  function updateScrub() {
    const pctVal = scrubber.value / 1000;
    const ts = minTs + pctVal * span;
    scrubMarker.style.left = (pctVal * 100) + '%';

    const diffHours = (Date.now() - ts) / (1000 * 60 * 60);
    const agoStr = diffHours < 1
      ? Math.round(diffHours * 60) + 'm ago'
      : diffHours.toFixed(1) + 'h ago';
    scrubAgo.textContent = agoStr;
    scrubTime.textContent = new Date(ts).toISOString().replace('T', ' ').substring(0, 19) + ' UTC';

    // Nearest event
    let nearest = events[0], minDist = Infinity;
    for (const ev of events) {
      const d = Math.abs(ev.ts_ms - ts);
      if (d < minDist) { minDist = d; nearest = ev; }
    }
    if (nearest) showDetail(nearest);
  }
  scrubber.addEventListener('input', updateScrub);

  // Park scrubber on primary suspect on load
  const suspectEv = events.find(
    e => e.table_name === suspect.table_name && String(e.version) === String(suspect.version)
  );
  if (suspectEv) {
    const pctVal = (suspectEv.ts_ms - minTs) / span;
    scrubber.value = Math.round(pctVal * 1000);
    updateScrub();
    showDetail(suspectEv);
  } else {
    updateScrub();
  }
}

function showDetail(ev) {
  const cls = classifyEvent(ev);
  document.getElementById('detail-title').textContent =
    `${ev.table_name} · version ${ev.version} · ${ev.hours_ago}h ago`;
  document.getElementById('detail-category').textContent =
    (cls || 'event').replace('-', ' ').toUpperCase();

  const body = document.getElementById('detail-body');
  body.innerHTML = '';
  addRow(body, 'Table (FQN)', ev.table_fqn || ev.table_name);
  addRow(body, 'Version', 'v' + ev.version);
  addRow(body, 'Updated By', ev.updated_by);
  addRow(body, 'When (relative)', ev.hours_ago + 'h ago');
  addRow(body, 'When (absolute)', new Date(ev.ts_ms).toISOString().replace('T',' ').substring(0,19) + ' UTC');
  addRow(body, 'Category', cls || 'generic');

  const diff = document.createElement('div');
  diff.className = 'detail-diff';
  diff.textContent = ev.change_summary;
  body.appendChild(diff);
}

function addRow(parent, k, v) {
  const row = document.createElement('div'); row.className = 'detail-row';
  const kk = document.createElement('div'); kk.className = 'k'; kk.textContent = k;
  const vv = document.createElement('div'); vv.className = 'v'; vv.textContent = v;
  row.appendChild(kk); row.appendChild(vv);
  parent.appendChild(row);
}
</script>
</body>
</html>
"""


def generate(
    evidence: Evidence,
    report: RootCauseReport,
    out_path: Path | str = "replay.html",
    timings: dict | None = None,
) -> Path:
    """Generate a standalone Incident Dossier HTML file."""
    out = Path(out_path).resolve()

    # Flatten all events with timestamps
    all_events: list[dict] = []
    for ut in evidence.upstream:
        for ve in ut.version_events:
            all_events.append({
                "table_name": ve.table_name,
                "table_fqn": ve.table_fqn,
                "version": ve.version,
                "ts_ms": ve.timestamp_ms,
                "hours_ago": ve.hours_ago,
                "updated_by": ve.updated_by,
                "change_summary": ve.change_summary,
            })
    for ve in evidence.suspicious_events:
        key = (ve.table_fqn, ve.version)
        if not any((e["table_fqn"], e["version"]) == key for e in all_events):
            all_events.append({
                "table_name": ve.table_name,
                "table_fqn": ve.table_fqn,
                "version": ve.version,
                "ts_ms": ve.timestamp_ms,
                "hours_ago": ve.hours_ago,
                "updated_by": ve.updated_by,
                "change_summary": ve.change_summary,
            })
    all_events.sort(key=lambda e: e["ts_ms"])

    import os
    om_url_full = os.environ.get("OM_URL", "http://localhost:8585/api/v1")
    # Derive the base UI URL from the API URL (strip /api/v1)
    om_base = om_url_full.rsplit("/api/", 1)[0]

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "om_base_url": om_base,
        "failure": {
            "test_name": evidence.test_name,
            "ts_ms": evidence.failure_timestamp_ms,
            "hours_ago": evidence.failure_hours_ago,
            "result": evidence.test_result_summary,
        },
        "affected": {
            "fqn": evidence.affected_table_fqn,
            "name": evidence.affected_table_fqn.rsplit(".", 1)[-1],
            "column": evidence.affected_column,
            "tier": evidence.affected_table_tier,
        },
        "report": {
            "verdict": report.verdict,
            "confidence": report.confidence,
            "explanation": report.root_cause_explanation,
            "primary_suspect": report.primary_suspect_event,
            "blast_radius": report.blast_radius_summary,
            "suggested_fix": report.suggested_fix,
            "evidence_chain": report.evidence_references,
        },
        "events": all_events,
        "downstream": [
            {"name": d.name, "fqn": d.fqn, "depth": d.depth, "tier": d.tier}
            for d in evidence.downstream
        ],
        "timings": timings or {},
    }

    html = _HTML_TEMPLATE.replace("__PAYLOAD__", json.dumps(payload))
    out.write_text(html, encoding="utf-8")
    return out