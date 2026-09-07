FROM golang:1.27.1-bookworm@sha256:648f440f42a0958804efb24df176f806f9d353b41f1c0627f666428e40310f6b AS dev
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
ENV GOTOOLCHAIN=local

FROM dev AS build
RUN CGO_ENABLED=0 go build -trimpath -o /out/forgeapi ./cmd/forgeapi && CGO_ENABLED=0 go build -trimpath -o /out/demo ./cmd/demo

FROM scratch AS runtime
COPY --from=build /out/forgeapi /forgeapi
COPY --from=build /out/demo /demo
USER 65532:65532
ENTRYPOINT ["/forgeapi"]
CMD ["api"]
