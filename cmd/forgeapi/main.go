package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"forgeapi/internal/auth"
	"forgeapi/internal/httpapi"
	"forgeapi/internal/local"
	"forgeapi/internal/orchestration"
	"forgeapi/internal/store"
	"go.temporal.io/sdk/client"
	"go.temporal.io/sdk/worker"
)

func main() {
	if err := run(); err != nil {
		slog.Error("local process stopped", "reason", err.Error())
		os.Exit(1)
	}
}

func run() error {
	if len(os.Args) != 2 {
		return errors.New("usage: forgeapi api|worker|migrate|health")
	}
	if os.Args[1] == "health" {
		c := http.Client{Timeout: 2 * time.Second}
		r, err := c.Get("http://127.0.0.1:8080/healthz")
		if err != nil {
			return errors.New("health check failed")
		}
		defer r.Body.Close()
		if r.StatusCode != 200 {
			return errors.New("unhealthy")
		}
		return nil
	}
	cfg, err := local.Read()
	if err != nil {
		return err
	}
	var authenticator auth.Authenticator
	if os.Args[1] == "api" {
		authenticator, err = auth.NewEntra(cfg.Entra)
		if err != nil {
			return err
		}
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	dbCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
	s, err := store.Open(dbCtx, cfg.DatabaseURL)
	cancel()
	if err != nil {
		return errors.New("PostgreSQL unavailable; start the local dependencies and check DATABASE_URL")
	}
	defer s.Pool.Close()
	slog.Warn("LOCAL ONLY: simulated compute; never expose or deploy this binary", "auth_mode", cfg.AuthMode)
	switch os.Args[1] {
	case "migrate":
		return s.Migrate(ctx)
	case "api":
		api := &httpapi.API{Repo: s, BaseURL: cfg.BaseURL, CursorKey: []byte(cfg.CursorKey), Auth: authenticator}
		server := &http.Server{Addr: cfg.Listen, Handler: api.Handler(), ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 10 * time.Second, WriteTimeout: 30 * time.Second, IdleTimeout: 60 * time.Second, MaxHeaderBytes: 16 * 1024}
		go func() {
			<-ctx.Done()
			shutdown, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			_ = server.Shutdown(shutdown)
		}()
		slog.Info("local API listening", "url", cfg.BaseURL)
		err := server.ListenAndServe()
		if errors.Is(err, http.ErrServerClosed) {
			return nil
		}
		return err
	case "worker":
		connect, cancel := context.WithTimeout(ctx, 10*time.Second)
		c, err := client.DialContext(connect, client.Options{HostPort: cfg.TemporalAddress})
		cancel()
		if err != nil {
			return errors.New("Temporal unavailable; start the local dependencies and check TEMPORAL_ADDRESS")
		}
		defer c.Close()
		w := worker.New(c, orchestration.TaskQueue, worker.Options{})
		w.RegisterWorkflow(orchestration.Execution)
		w.RegisterActivity(&orchestration.Activities{Store: s})
		if err := w.Start(); err != nil {
			return fmt.Errorf("worker startup failed")
		}
		defer w.Stop()
		orchestration.Dispatch(ctx, s, c)
		return nil
	default:
		return errors.New("unknown command; use api, worker, migrate, or health")
	}
}
