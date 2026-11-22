# Pilot gateway on Fly.io

One always-on `shared-cpu-1x` machine (256 MB) running the released image in `mode: shadow`, the gatelog on a 1 GB
volume. It decides and records every PR of the repositories whose webhooks point at it and writes nothing to GitHub.

```bash
cd deploy/fly
fly launch --no-deploy --copy-config --name mergeone-pilot --region fra
fly volumes create mergeone_data --size 1 --region fra
fly secrets set MERGEONE_WEBHOOK_SECRET="$(openssl rand -hex 32)" GITHUB_TOKEN=… TYPESAFE_API_KEY=… \
                MERGEONE_PORTAL_TOKEN=… MERGEONE_METRICS_TOKEN="$(openssl rand -hex 24)"
fly deploy
curl https://mergeone-pilot.fly.dev/healthz      # {"ok":true,"mode":"shadow",…}
```

Then, per repository: Settings → Webhooks → Add webhook — payload URL `https://<app>.fly.dev/webhook/github`,
content type `application/json`, the same secret, event "Pull requests".

- `GITHUB_TOKEN`: a fine-grained token with read access to pull requests and contents of the pilot repositories
  (shadow mode never writes).
- The policy is `policy.yaml` here (the shipped default, `mode: shadow`, portal block). Edit, `fly deploy`.
- Read the log: `fly ssh console -C "mergeone stats --log /data/gatelog.jsonl"`; copy it off with `fly sftp get`.
- The image is distroless: there is no shell; `fly ssh console -C` runs the binary directly.
- PR memory is written beside the policy by default, and the image's policy directory is read-only: `fly.toml` sets
  `MERGEONE_STATE_DIR = "/data"`, so memory lives in `/data/memory` on the volume and survives a deploy. (Images up to
  0.7.0 do not read the variable; the `Dockerfile` then linked `/policy/.mergeone` onto the volume, same directory.)
- A machine on a Fly trial account is stopped after five minutes and started again by the next request: webhooks
  are not lost, but the gateway is not always on until the account has a payment method.
