# Test-only TLS material

No private key is stored in this repository.

Tests and the local P7 demo generate a throwaway self-signed localhost
certificate with `backnest.tls.ensure_test_cert()` under the git-ignored
`.backnest/tls/` directory, using either the optional `cryptography` package or
the `openssl` CLI.

For a real deployment, provide a certificate issued by a trusted CA to
`scripts/remote_server.py --cert/--key`. Clients should use the system trust
store or an explicit `caFile`.

Do not commit generated `*.pem` or `*.key` files.
