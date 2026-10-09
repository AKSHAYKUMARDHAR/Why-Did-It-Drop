# Why Did It Drop?

An analytics agent that explains why a product metric moved, and says when it can't. Code computes
every number, checks whether the change is real and whether tracking broke before looking for a cause,
and verifies every figure in the model's answer against a query result. It is scored on a public
benchmark of incidents planted in real e-commerce event data (Google's GA4 demo store), with its
failures published.

- Product requirements: [docs/PRD.md](docs/PRD.md)
- Status: in development.

Built with Claude Code, after [RBI Circular Radar](https://github.com/AKSHAYKUMARDHAR/RBI-Circular-Radar),
[Will My Policy Pay?](https://github.com/AKSHAYKUMARDHAR/Will-My-Policy-Pay),
[Is This a Scam?](https://github.com/AKSHAYKUMARDHAR/Is-This-A-Scam) and
[UPI Triage Agent](https://github.com/AKSHAYKUMARDHAR/UPI-Triage-Agent).
