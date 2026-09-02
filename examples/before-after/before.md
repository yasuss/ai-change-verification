# Before: AI-assisted billing retry change

The assistant changed the billing worker so a failed payment is retried three times and a customer-facing message is emitted after the final attempt. The diff updates the retry loop and adds a timeout, but the proposal does not show:

- which provider error classes are safe to retry;
- whether the queue is at-least-once and can duplicate a charge;
- a deterministic check for timeout plus worker restart;
- the intended behavior for an already-settled payment.

A reviewer still has to recover the incident context, inspect the payment contract, and decide whether the evidence is sufficient.
