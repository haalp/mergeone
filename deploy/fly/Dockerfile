# The pilot gateway: the released image plus a policy baked in. Build context is this directory.
ARG MERGEONE_VERSION=0.7.0

# PR memory lives in .mergeone/ beside the policy; the image is read-only there and has no shell,
# so the directory is a link onto the volume (memory then survives a deploy, like the gatelog).
FROM busybox:1.36 AS layout
RUN mkdir /policy && ln -s /data /policy/.mergeone

FROM ghcr.io/haalp/mergeone:${MERGEONE_VERSION}
COPY --from=layout /policy /policy
COPY policy.yaml /policy/.mergeone.yaml
CMD ["serve", "--policy", "/policy/.mergeone.yaml", "--log", "/data/gatelog.jsonl", "--addr", ":8787", "--assets", "/assets"]
