# Pointer Batch Reordering

**Date:** 2026-08-24  
**Application:** Five Pointers at Snake Mountain  
**Status:** Implemented and published; retest pending

## Observation

Four browser windows on one machine exercised four independent pointer clients. Their displays converged strongly, but clients One and Four omitted some of the same strokes.

The shared omission mattered. This was not merely visible pointer jitter or a difference in local drawing. Multiple receivers had failed to present the same transmitted material.

## Failure mechanism

Pointer samples carry monotonically increasing sequence numbers and travel in batches. Backend invocations and broadcasts may overlap, so batch 12 can arrive before batch 11.

The client previously advanced its sequence marker when a sample **arrived**. If batch 12 arrived first, the marker advanced. Batch 11 then appeared stale and was discarded even though none of its samples had been rendered. One reordered delivery could therefore erase most or all of a stroke.

## Increment

The remote receiver now:

- retains samples in the existing 45-millisecond presentation buffer;
- sorts queued samples by sequence;
- suppresses duplicate queued samples; and
- rejects a late sample only when a newer sequence has already been rendered.

No backend, database, protocol, or infrastructure change was introduced. Local drawing remains immediate. Remote presentation retains the same small buffer.

## Claim boundary

The change removes one identified client-side loss mechanism. It does not establish perfect delivery, eliminate every source of missed strokes, or prove convergence under load.

The implementation is published in commit `686e124`. A fresh four-client exercise is required before the defect may be considered corrected.