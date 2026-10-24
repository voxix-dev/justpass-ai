# Job lifecycle

The flow is async and one-shot: the student submits, leaves, and is notified in the app when the
result is ready. They accept it or send feedback. Feedback starts a new round of the same job.

```
received ──dispatch──▶ processing ──result saved──▶ awaiting_review ──accept──▶ accepted
                          │   ▲                        │      │
              error after │   └──── feedback ──────────┘      │ 48 h, no reply
                  1 retry ▼                                    ▼
                        failed                              expired ──feedback──▶ processing
```

| State | Agent actor | Notes |
|-------|-------------|-------|
| `received` | none | Upload saved, job row written |
| `processing` | running | One round is in progress |
| `awaiting_review` | hibernated | Snapshot kept until the review deadline |
| `accepted` | gone | Final; snapshot deleted |
| `expired` | gone | Result kept, snapshot deleted. Late feedback starts a fresh actor |
| `failed` | gone | Final, after one retry. The student is notified |

Transitions are defined once in `shared.models.TRANSITIONS`; the API refuses anything else. Load the
job with `SELECT ... FOR UPDATE` before changing its status, so two requests (for example feedback
and the expiry sweeper) can't both win.

## Rounds

- Round 1 answers the request. Each feedback adds a round to the same job.
- A round's answer is its `final` artifact (at most one per round).
- Feedback within the snapshot's lifetime restores the snapshot and appends the feedback as a new
  message. After that, a fresh actor rebuilds the conversation from the transcript in Postgres.

## Timing

- The actor hibernates the moment the result is saved; there is no idle wait.
- Review window: 48 h. A storage lifecycle rule deletes snapshots older than 3 days as a safety net.
- Notifications are in-app polling (about every 5 s). The poll endpoint repeats a few seconds before
  `updated_since` and the client de-duplicates by id, because commit time and `updated_at` can
  differ. Email and push come later.
