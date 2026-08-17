# Engineering handbook

Production deployments require two reviewers. Send five percent of traffic to
the canary and watch latency and error rate for fifteen minutes.

If either service-level indicator regresses, roll back immediately and attach
the dashboard snapshot to the release record.
