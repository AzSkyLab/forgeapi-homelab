package store

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"forgeapi/internal/deployment"
	"forgeapi/internal/execution"
	"github.com/jackc/pgx/v5"
)

func (s *Store) CreateDeployment(ctx context.Context, owner, key, hash string, t deployment.Target) ([]byte, error) {
	if owner != t.Owner {
		return nil, deployment.ErrDenied
	}
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, "SELECT pg_advisory_xact_lock(706010005)"); err != nil {
		return nil, err
	}
	digest := fmt.Sprintf("sha256:%x", sha256.Sum256([]byte(owner+"\x00"+key)))
	var oldHash string
	var receipt []byte
	err = tx.QueryRow(ctx, "SELECT request_hash,receipt FROM local_deployments WHERE idempotency_digest=$1", digest).Scan(&oldHash, &receipt)
	if err == nil {
		if oldHash != hash {
			return nil, deployment.ErrConflict
		}
		return receipt, nil
	}
	if !errors.Is(err, pgx.ErrNoRows) {
		return nil, err
	}
	if t.Executor.Validate() != nil {
		return nil, deployment.ErrDenied
	}
	var exists bool
	if err = tx.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM local_deployments WHERE resource_key=$1)", strings.ToLower(t.ResourceID())).Scan(&exists); err != nil {
		return nil, err
	}
	if exists {
		return nil, deployment.ErrConflict
	}
	r := deployment.Record{ID: execution.ID("dep_"), PatternID: deployment.PatternID, Target: t}
	r.Transition("accepted")
	b, _ := json.Marshal(r)
	if _, err = tx.Exec(ctx, "INSERT INTO local_deployments(id,owner,idempotency_digest,request_hash,resource_key,state,body,receipt) VALUES($1,$2,$3,$4,$5,$6,$7,$7)", r.ID, owner, digest, hash, strings.ToLower(t.ResourceID()), r.State, b); err != nil {
		return nil, err
	}
	if _, err = tx.Exec(ctx, "INSERT INTO local_deployment_outbox(deployment_id,phase) VALUES($1,'plan')", r.ID); err != nil {
		return nil, err
	}
	if err = tx.QueryRow(ctx, "SELECT receipt FROM local_deployments WHERE id=$1", r.ID).Scan(&receipt); err != nil {
		return nil, err
	}
	return receipt, tx.Commit(ctx)
}
func (s *Store) GetDeployment(ctx context.Context, id string) (deployment.Record, error) {
	var r deployment.Record
	var b []byte
	err := s.Pool.QueryRow(ctx, "SELECT body FROM local_deployments WHERE id=$1", id).Scan(&b)
	if errors.Is(err, pgx.ErrNoRows) {
		return r, deployment.ErrNotFound
	}
	if err != nil {
		return r, err
	}
	err = json.Unmarshal(b, &r)
	return r, err
}

// UpdateDeployment serializes projection/approval/outbox changes; external work
// happens only after commit, never while holding a database transaction.
func (s *Store) UpdateDeployment(ctx context.Context, id string, fn func(*deployment.Record) error) error {
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	var b []byte
	err = tx.QueryRow(ctx, "SELECT body FROM local_deployments WHERE id=$1 FOR UPDATE", id).Scan(&b)
	if errors.Is(err, pgx.ErrNoRows) {
		return deployment.ErrNotFound
	}
	if err != nil {
		return err
	}
	var r deployment.Record
	if err = json.Unmarshal(b, &r); err != nil {
		return err
	}
	previous := r.State
	if err = fn(&r); err != nil {
		return err
	}
	b, _ = json.Marshal(r)
	if _, err = tx.Exec(ctx, "UPDATE local_deployments SET state=$2,body=$3,updated_at=now() WHERE id=$1", id, r.State, b); err != nil {
		return err
	}
	if previous == "planned" && r.State == "apply_queued" {
		if _, err = tx.Exec(ctx, "INSERT INTO local_deployment_outbox(deployment_id,phase) VALUES($1,'apply')", id); err != nil {
			return err
		}
	}
	return tx.Commit(ctx)
}
func (s *Store) ApproveDeployment(ctx context.Context, id, owner, digest string, target deployment.Target) error {
	return s.UpdateDeployment(ctx, id, func(r *deployment.Record) error {
		if r.Target.Owner != owner {
			return deployment.ErrNotFound
		}
		if r.Target != target || r.PlanDigest != digest || digest == "" {
			return deployment.ErrConflict
		}
		if r.State == "apply_queued" || r.State == "applying" || r.State == "succeeded" {
			return nil
		}
		if r.State != "planned" || !time.Now().Before(r.PlanExpiresAt) {
			return deployment.ErrConflict
		}
		r.Transition("apply_queued")
		return nil
	})
}

type DeploymentIntent struct{ ID, Phase string }

func (s *Store) PendingDeployments(ctx context.Context) ([]DeploymentIntent, error) {
	rows, err := s.Pool.Query(ctx, "SELECT deployment_id,phase FROM local_deployment_outbox WHERE NOT delivered ORDER BY deployment_id,phase LIMIT 10")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []DeploymentIntent{}
	for rows.Next() {
		var i DeploymentIntent
		if err = rows.Scan(&i.ID, &i.Phase); err != nil {
			return nil, err
		}
		out = append(out, i)
	}
	return out, rows.Err()
}
func (s *Store) DeploymentDispatched(ctx context.Context, id, phase string) error {
	_, err := s.Pool.Exec(ctx, "UPDATE local_deployment_outbox SET delivered=true WHERE deployment_id=$1 AND phase=$2", id, phase)
	return err
}
func (s *Store) ActiveDeployments(ctx context.Context) ([]DeploymentIntent, error) {
	rows, err := s.Pool.Query(ctx, "SELECT id,CASE WHEN state='planning' THEN 'plan' ELSE 'apply' END FROM local_deployments WHERE state IN ('planning','applying')")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []DeploymentIntent{}
	for rows.Next() {
		var i DeploymentIntent
		if err = rows.Scan(&i.ID, &i.Phase); err != nil {
			return nil, err
		}
		out = append(out, i)
	}
	return out, rows.Err()
}
