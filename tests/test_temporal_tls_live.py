"""Opt-in Temporal TLS/mTLS over a real handshake against a real Temporal server.

temporalio's local dev server has no TLS listener, so a small in-test TLS-terminating proxy
(requires a client certificate, ALPN h2) fronts it. The server certificate's only SAN is
`temporal.internal`, so connecting to 127.0.0.1 verifies only if `temporal_tls_server_name` is
honoured. Everything here is throwaway: certificates are generated in tmp_path.
"""

import asyncio
import datetime
import ssl
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from temporalio.testing import WorkflowEnvironment

from app.dispatch import OperationDispatcher, connect_temporal, get_dispatcher
from app.main import app
from app.settings import settings
from app.worker import build_worker
from tests.operation_support import phase_done

SERVER_NAME = "temporal.internal"
CLIENT_CN = "forgeapi-test-client"


def _name(cn):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _issue(cn, issuer_cert, issuer_key, *, ca=False, san=None):
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.datetime.now(datetime.UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(_name(cn))
        .issuer_name(issuer_cert.subject if issuer_cert else _name(cn))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
    )
    if san:
        builder = builder.add_extension(x509.SubjectAlternativeName([x509.DNSName(san)]), False)
    return builder.sign(issuer_key or key, hashes.SHA256()), key


def _write(directory, stem, cert, key=None):
    pem = directory / f"{stem}.pem"
    pem.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    if key is None:
        return pem, None
    key_pem = directory / f"{stem}.key"
    key_pem.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return pem, key_pem


@pytest.fixture
def pki(tmp_path):
    d = tmp_path / "pki"
    d.mkdir()
    ca, ca_key = _issue("forgeapi-test-ca", None, None, ca=True)
    server, server_key = _issue("temporal-server", ca, ca_key, san=SERVER_NAME)
    client, client_key = _issue(CLIENT_CN, ca, ca_key)
    other_ca, _ = _issue("unrelated-ca", None, None, ca=True)
    return {
        "ca": _write(d, "ca", ca)[0],
        "other_ca": _write(d, "other-ca", other_ca)[0],
        "server": _write(d, "server", server, server_key),
        "client": _write(d, "client", client, client_key),
    }


@asynccontextmanager
async def tls_proxy(env, pki):
    """mTLS terminator on 127.0.0.1:<free port> forwarding plaintext to the dev server."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(*pki["server"])
    context.load_verify_locations(pki["ca"])
    context.verify_mode = ssl.CERT_REQUIRED
    context.set_alpn_protocols(["h2"])  # gRPC needs HTTP/2
    host, port = env.client.service_client.config.target_host.rsplit(":", 1)
    peers: list[str] = []

    async def pump(reader, writer):
        with suppress(Exception):
            while data := await reader.read(65536):
                writer.write(data)
                await writer.drain()
        with suppress(Exception):
            writer.close()

    async def handle(reader, writer):
        cert = writer.get_extra_info("peercert")
        peers.append(dict(pair[0] for pair in cert["subject"])["commonName"])
        upstream_reader, upstream_writer = await asyncio.open_connection(host, int(port))
        await asyncio.gather(
            pump(reader, upstream_writer), pump(upstream_reader, writer), return_exceptions=True
        )

    server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=context)
    try:
        yield f"127.0.0.1:{server.sockets[0].getsockname()[1]}", peers
    finally:
        server.close()


def _tls_settings(monkeypatch, pki, address, **overrides):
    values = {
        "temporal_address": address,
        "temporal_tls": True,
        "temporal_tls_ca_path": pki["ca"],
        "temporal_tls_cert_path": pki["client"][0],
        "temporal_tls_key_path": pki["client"][1],
        "temporal_tls_server_name": SERVER_NAME,
    } | overrides
    for name, value in values.items():
        monkeypatch.setattr(settings, name, value)


def test_deploy_end_to_end_over_mtls(pki, monkeypatch):
    async def scenario():
        async with (
            await WorkflowEnvironment.start_local() as env,
            tls_proxy(env, pki) as (address, peers),
        ):
            _tls_settings(monkeypatch, pki, address)
            await OperationDispatcher().ready()
            client = await connect_temporal()
            previous = app.dependency_overrides.copy()
            # No client injected: the API connects itself through connect_temporal().
            app.dependency_overrides[get_dispatcher] = lambda: OperationDispatcher()
            try:
                async with (
                    build_worker(client),
                    httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app), base_url="http://test"
                    ) as api,
                ):
                    intent = {
                        "pattern": "local-file",
                        "inputs": {"filename": "tls.txt", "content": "over mtls"},
                    }
                    r = await api.post(
                        "/operations", json=intent, headers={"Idempotency-Key": "tls"}
                    )
                    assert r.status_code == 202
                    op = r.json()
                    await phase_done(client, op["id"], "plan")
                    planned = (await api.get(op["links"]["self"])).json()
                    assert planned["state"] == "planned"
                    r = await api.post(
                        op["links"]["execute"], json={"plan_digest": planned["plan_digest"]}
                    )
                    assert r.status_code == 202
                    await phase_done(client, op["id"], "apply")
                    done = (await api.get(op["links"]["self"])).json()
            finally:
                app.dependency_overrides = previous
            assert done["state"] == "succeeded"
            assert Path(done["outputs"]["path"]).read_text() == "over mtls"
            assert peers and set(peers) == {CLIENT_CN}

    asyncio.run(scenario())


def test_misconfigured_tls_is_refused(pki, monkeypatch):
    cases = {
        "no client cert": {"temporal_tls_cert_path": None, "temporal_tls_key_path": None},
        "unrelated CA": {"temporal_tls_ca_path": pki["other_ca"]},
        "no server name": {"temporal_tls_server_name": None},
        "plaintext to TLS port": {"temporal_tls": False},
    }

    async def probe():
        client = await connect_temporal()
        await client.service_client.check_health()

    async def scenario():
        async with (
            await WorkflowEnvironment.start_local() as env,
            tls_proxy(env, pki) as (address, peers),
        ):
            _tls_settings(monkeypatch, pki, address)
            await asyncio.wait_for(probe(), 15)  # the good configuration works
            for overrides in cases.values():
                _tls_settings(monkeypatch, pki, address, **overrides)
                with pytest.raises(Exception):  # noqa: B017, PT011 - any failure is a refusal
                    await asyncio.wait_for(probe(), 15)
                # ready() is the readiness probe's path: it must raise too, never pass.
                with pytest.raises(Exception):  # noqa: B017, PT011
                    await OperationDispatcher().ready()

    asyncio.run(scenario())
