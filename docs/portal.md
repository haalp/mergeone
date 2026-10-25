# Portal

The hosted portal (queue of PRs that need a person, the PR braid, the health page) lives in a separate, private
repository: `haalp/mergeone-portal`. This repository stays the open-source gateway and benchmark. The gateway talks to
the portal through one optional block in `.mergeone.yaml`:

```yaml
portal:
  url: https://app.mergeone.dev
  token: ${MERGEONE_PORTAL_TOKEN}   # issued per repository when you connect it in the portal
```

With it set, every gatelog record (`store.Record`, head-SHA keyed, raw and calibrated probabilities, verdict, rule,
route, matched candidate) is also posted to the portal; the gateway stays the source of truth and works unchanged
without it. The portal never merges or closes anything; resolutions made there come back as the same labels and
comments the gateway's verdict actions already use.

API the gateway serves and the portal reads (also usable without the portal): `docs/POLICY.md` → `/stats`,
and the read endpoints listed in `ROADMAP.md` v0.3.
