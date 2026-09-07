FROM golang:1.27.1-bookworm@sha256:648f440f42a0958804efb24df176f806f9d353b41f1c0627f666428e40310f6b AS dev
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
ENV GOTOOLCHAIN=local

# The browser's callback runs on the laptop, not Docker's private loopback.
# The helper is not copied into the API runtime image.
FROM dev AS demo-build
ARG CLIENT_OS
ARG CLIENT_ARCH
RUN CGO_ENABLED=0 GOOS=${CLIENT_OS} GOARCH=${CLIENT_ARCH} go build -trimpath -o /out/forgeapi-demo ./cmd/demo

FROM dev AS build
RUN CGO_ENABLED=0 go build -trimpath -o /out/forgeapi ./cmd/forgeapi

FROM scratch AS runtime
COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=build /out/forgeapi /forgeapi
USER 65532:65532
ENTRYPOINT ["/forgeapi"]
CMD ["api"]
