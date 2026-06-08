#!/bin/bash
# Obtain Let's Encrypt cert for vizbriz.com and enable Apache HTTPS.
set -euo pipefail

DOMAIN="vizbriz.com"
EC2_IP="3.132.113.74"
WEBROOT="/var/www/letsencrypt"
EMAIL="info@vizbriz.com"

echo "Checking DNS for ${DOMAIN}..."
RESOLVED=$(dig +short "$DOMAIN" A 2>/dev/null | tr '\n' ' ')
CNAME=$(dig +short "$DOMAIN" CNAME 2>/dev/null | tr '\n' ' ')

if echo "$RESOLVED $CNAME" | grep -q "$EC2_IP"; then
  echo "DNS includes this server (${EC2_IP}). Using HTTP-01 (webroot)."
  sudo certbot certonly --webroot -w "$WEBROOT" -d "$DOMAIN" \
    --non-interactive --agree-tos -m "$EMAIL" \
    --deploy-hook "systemctl reload httpd"
else
  echo "DNS does not point to this EC2 (${EC2_IP})."
  echo "  Current A: ${RESOLVED:-none}"
  echo "  Current CNAME: ${CNAME:-none}"
  echo ""
  echo "Option A (recommended): Set an A record:"
  echo "  @  ->  ${EC2_IP}"
  echo "  (remove CNAME to the ELB), wait a few minutes, re-run this script."
  echo ""
  echo "Option B: Use DNS-01 (TXT record) — run:"
  echo "  sudo certbot certonly --manual --preferred-challenges dns -d ${DOMAIN} \\"
  echo "    --agree-tos -m ${EMAIL} \\"
  echo "    --manual-auth-hook /home/ec2-user/vizbriz/scripts/acme-dns-auth-hook.sh"
  exit 1
fi

sudo tee /etc/httpd/conf.d/vizbriz-le-ssl.conf >/dev/null <<EOF
# ${DOMAIN} — HTTPS (Let's Encrypt)
<VirtualHost *:443>
    ServerName ${DOMAIN}

    SSLEngine on
    SSLCertificateFile /etc/letsencrypt/live/${DOMAIN}/fullchain.pem
    SSLCertificateKeyFile /etc/letsencrypt/live/${DOMAIN}/privkey.pem
    Include /etc/letsencrypt/options-ssl-apache.conf

    ProxyPreserveHost On
    ProxyRequests Off
    ProxyPass / http://127.0.0.1:4173/
    ProxyPassReverse / http://127.0.0.1:4173/

    <Proxy *>
        Require all granted
    </Proxy>

    ErrorLog /var/log/httpd/vizbriz-ssl-error.log
    CustomLog /var/log/httpd/vizbriz-ssl-access.log combined
</VirtualHost>
EOF

# HTTP → HTTPS redirect (keep ACME path on port 80)
sudo tee /etc/httpd/conf.d/vizbriz.conf >/dev/null <<'EOF'
<VirtualHost *:80>
    ServerName vizbriz.com

    Alias /.well-known/acme-challenge /var/www/letsencrypt/.well-known/acme-challenge
    <Directory /var/www/letsencrypt/.well-known/acme-challenge>
        Require all granted
        Options -Indexes
    </Directory>

    RewriteEngine On
    RewriteCond %{REQUEST_URI} !^/\.well-known/acme-challenge/
    RewriteRule ^ https://%{HTTP_HOST}%{REQUEST_URI} [R=301,L]

    ProxyPreserveHost On
    ProxyRequests Off
    ProxyPass /.well-known/acme-challenge !
    ProxyPass / http://127.0.0.1:4173/
    ProxyPassReverse / http://127.0.0.1:4173/

    <Proxy *>
        Require all granted
    </Proxy>

    ErrorLog /var/log/httpd/vizbriz-error.log
    CustomLog /var/log/httpd/vizbriz-access.log combined
</VirtualHost>
EOF

sudo rm -f /etc/httpd/conf.d/vizbrizbriz.conf /etc/httpd/conf.d/vizlanding.conf /etc/httpd/conf.d/vizlanding-le-ssl.conf

sudo apachectl configtest
sudo systemctl reload httpd
echo "HTTPS enabled for https://${DOMAIN}/"
