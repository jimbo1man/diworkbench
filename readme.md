# DI Workbench

A structured workflow for using multiple LLMs without drift, noise, or hallucinated architecture.

This is how I run AI-assisted engineering:

* one model drafts
* one model challenges
* output is constrained, not conversational
* decisions are explicit and reviewable

No prompt spaghetti.
No infinite loops.
No “vibe-based” iteration.

Just controlled, repeatable refinement of a single artifact.

---

## Flow

Spec → Claude review → ChatGPT adjudication → Final spec

* **Claude**: APPROVE | REVISE | REJECT (with reasoning)
* **ChatGPT**: evaluates and produces a single implementation-ready spec
* **Human**: approves or rejects

One pass. No loops.

---

## What this is

DI Workbench is not a product.

It’s a pattern for keeping AI-assisted work:

* structured
* adversarial (in a useful way)
* bounded by contracts instead of conversation

The goal is simple:
take a vague idea and turn it into something you can actually build—without losing rigor along the way.

---

## Why this exists

Most AI workflows break down in predictable ways:

* prompts drift
* context gets noisy
* models agree too easily
* outputs look good but don’t hold up

This approach forces friction where it matters:

* explicit review
* clear states (approve / revise / reject)
* a single, clean artifact at the end

---

## Core principles

* **One dominant artifact**
  Everything exists to refine a single spec.

* **Hard gates**
  Bad specs stop early. They don’t propagate.

* **Adversarial collaboration**
  Models don’t “help”—they challenge and refine.

* **No hidden reasoning**
  Decisions are visible and attributable.

* **Human final say**
  Nothing ships without approval.

---

## Output

Each run produces:

* Claude state + reasoning
* ChatGPT state
* **Final spec (implementation-ready)**

The final spec is the product.

---

## Status

Actively used and evolving.

This repo is intentionally minimal—focused on the pattern, not the polish.
