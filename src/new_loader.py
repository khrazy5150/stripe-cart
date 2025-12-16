from new_loader import get_tenant, get_secret, encrypt_if_needed, upsert_partial, resolved_source

# Load tenant without decrypting (cached):
tenant = get_tenant("acme")

# Load and decrypt stripe secret only:
stripe_secret = get_secret("acme", "stripe_secret_key")

# Or decrypt all known secrets:
tenant_full = get_tenant("acme", decrypt=True)

# Save/update secrets safely:
updates = {
    "stripe_secret_key": encrypt_if_needed("sk_live_..."),
    "stripe_publishable_key": "pk_live_...",
}
upsert_partial("acme", updates)

print(resolved_source())  # {'environment': 'prod', 'table': 'stripe-keys-prod', 'region': 'us-west-2'}
