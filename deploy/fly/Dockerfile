# The pilot gateway: the released image plus a policy baked in. Build context is this directory.
ARG MERGEONE_VERSION=0.5.1
FROM ghcr.io/haalp/mergeone:${MERGEONE_VERSION}
COPY policy.yaml /policy/.mergeone.yaml
CMD ["serve", "--policy", "/policy/.mergeone.yaml", "--log", "/data/gatelog.jsonl", "--addr", ":8787", "--assets", "/assets"]
