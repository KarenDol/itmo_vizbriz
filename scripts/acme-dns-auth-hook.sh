#!/bin/bash
# Certbot manual DNS auth hook — writes instructions and waits for DNS propagation.
set -euo pipefail
OUT="/home/ec2-user/ACME_DNS_INSTRUCTIONS.txt"
RECORD="_acme-challenge.vizbriz.com"
{
  echo "Add this DNS TXT record at your DNS provider:"
  echo "  Name:  _acme-challenge  (or _acme-challenge.vizbriz.com)"
  echo "  Type:  TXT"
  echo "  Value: ${CERTBOT_VALIDATION}"
  echo ""
  echo "Waiting 5 minutes for propagation..."
} | tee -a "$OUT"
sleep 300
