# The pilot gateway: the released image plus a policy baked in. Build context is this directory.
# PR memory goes to the volume through MERGEONE_STATE_DIR (fly.toml [env]); the policy directory stays read-only.
ARG MERGEONE_VERSION=0.8.0
FROM ghcr.io/haalp/mergeone:${MERGEONE_VERSION}
COPY policy.yaml /policy/.mergeone.yaml
CMD ["serve", "--policy", "/policy/.mergeone.yaml", "--log", "/data/gatelog.jsonl", "--addr", ":8787", "--assets", "/assets"]
