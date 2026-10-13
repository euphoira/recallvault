# Security policy

RecallVault is a localhost, single-process development workbench. It has no login or network multi-tenant authorization. namespace/user IDs are scope filters and can be selected by any trusted local caller. Never expose the service directly to untrusted networks.

The server restricts Host to loopback names, rejects foreign Origin requests, enforces a 256 KiB body limit and provides a restrictive Content Security Policy. These controls reduce browser-origin attacks; they do not replace identity authentication or defend against malicious local programs.

User and model text is untrusted. The server verifies exact user source quotes and retrieved memory citations. It exposes only read-only memory tools to the assistant, and it does not follow endpoint redirects. Credentials remain in environment configuration; `.env`, databases and runtime logs are excluded from Git.

Forgetting clears application-accessible source content, all versions of the selected key, vectors and scoped trace text. It does not guarantee forensic disk/WAL/backup erasure or erase data already sent to external providers. Embeddings and live mode transmit data; leave them disabled for entirely offline operation.

Please report vulnerabilities using GitHub private vulnerability reporting if available, or contact the maintainer through the GitHub profile. Do not attach real keys, personal conversations or sensitive databases to public issues.
