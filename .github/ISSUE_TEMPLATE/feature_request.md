---
name: Feature request
about: Suggest something this tool should read, compute, or display
title: ""
labels: enhancement
assignees: ""
---

## What's the actual mixed-kit scenario this would help with?

<!--
This tool is scoped narrowly on purpose (see the wiki's "FAQ and Design
Decisions" page for the reasoning behind several existing boundaries --
read-only, no channel-topology guessing, no vendor-voltage OC suggestions,
etc). A request grounded in a specific real situation is much easier to
evaluate than a general "it would be nice if" -- what were you trying to
do, and where did the tool fall short?
-->

## What would you expect the tool to show or do differently?

## If this involves a new SPD byte/field

Per `CONTRIBUTING.md`: any new byte offset needs to be independently
verified (cross-checked against a second source, ideally with a real
SPD dump) before it's trustworthy enough to include -- if you already
have a source for the offset (a datasheet, another tool's source, JEDEC
spec section), linking it here saves a round-trip.
