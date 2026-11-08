# mergeone gateway image: one static binary plus the release assets (policy template, prompts, calibration
# maps). Built by the release workflow as ghcr.io/haalp/mergeone:<tag>; build locally with
#   docker build -t mergeone .
# Run the webhook mode with the repository's policy mounted:
#   docker run -p 8787:8787 -v $PWD/.mergeone.yaml:/policy/.mergeone.yaml -v mergeone-data:/data \
#     -e GITHUB_TOKEN -e MERGEONE_WEBHOOK_SECRET -e PERPLEXITY_API_KEY \
#     ghcr.io/haalp/mergeone:latest serve --policy /policy/.mergeone.yaml --log /data/gatelog.jsonl --addr :8787
FROM --platform=$BUILDPLATFORM golang:1.24-alpine AS build
ARG TARGETOS TARGETARCH VERSION=dev
WORKDIR /src
COPY gate/go.mod gate/go.sum ./gate/
RUN cd gate && go mod download
COPY gate ./gate
RUN cd gate && CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH \
    go build -trimpath -ldflags="-s -w -X main.version=${VERSION}" -o /out/mergeone ./cmd/mergeone

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/mergeone /usr/local/bin/mergeone
# release assets, where buildGate looks for them when --assets is not given (next to the policy, or here)
COPY mergeone/gate/policy.yaml mergeone/gate/calibration_*.json /assets/
COPY prompts/jev_v2.json prompts/jev_v3.json prompts/jev_v4.json prompts/jev_pair.json \
     prompts/haiku_v2.md prompts/haiku_v3.md prompts/haiku_v4.md prompts/haiku_pair.md prompts/models.yaml /assets/
ENV MERGEONE_ASSETS=/assets
VOLUME ["/data"]
EXPOSE 8787
USER nonroot:nonroot
ENTRYPOINT ["/usr/local/bin/mergeone"]
CMD ["serve", "--policy", "/policy/.mergeone.yaml", "--log", "/data/gatelog.jsonl", "--addr", ":8787", "--assets", "/assets"]
