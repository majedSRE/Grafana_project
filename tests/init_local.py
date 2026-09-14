"""Initialize only the isolated validation volumes; never run on host data."""
from pathlib import Path
import os
import secrets

for service, uid in (("prometheus", 65534), ("grafana", 472), ("loki", 10001),
                     ("tempo", 10001), ("alloy", 0), ("alertmanager", 65534)):
    path = Path("/state") / service
    if not path.is_mount():
        raise SystemExit(f"Refusing to initialize a non-volume path: {path}")
    os.chown(path, uid, uid)
    os.chmod(path, 0o750)

secret_dir = Path("/state/secrets")
if not secret_dir.is_mount():
    raise SystemExit("Missing isolated secret volume")
os.chown(secret_dir, 0, 472)
os.chmod(secret_dir, 0o750)
secret_file = secret_dir / "grafana_admin_password"
if not secret_file.exists():
    descriptor = os.open(secret_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(secrets.token_urlsafe(32))
    os.chown(secret_file, 0, 472)
print("Validation volume ownership and private test credential are ready.")
