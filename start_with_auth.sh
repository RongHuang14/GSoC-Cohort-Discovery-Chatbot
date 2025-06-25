#!/bin/bash

# PCDC GraphQL Query Generator with Official Chat History
echo "[INFO] Starting PCDC GraphQL Query Generator with Official Chat History"

# Check if .env file exists
if [ ! -f .env ]; then
    echo "⚠️  .env file not found. Creating one..."
    touch .env
fi

# Generate authentication secret if not exists
if ! grep -q "CHAINLIT_AUTH_SECRET" .env; then
    echo "[INFO] Generating authentication secret..."
    SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
    echo "CHAINLIT_AUTH_SECRET=$SECRET" >> .env
    echo "[INFO] Authentication secret generated and saved to .env"
fi

# Update config.toml with the generated secret
echo "[INFO] Updating configuration with authentication secret..."
SECRET=$(grep "CHAINLIT_AUTH_SECRET" .env | cut -d'=' -f2)
sed -i "s/secret = \"your-secret-here-replace-with-real-secret\"/secret = \"$SECRET\"/" .chainlit/config.toml

echo "[INFO] Configuration updated"

# Show login credentials
echo ""
echo "[INFO] Available login credentials:"
echo "   Username: admin   | Password: admin123"
echo "   Username: user    | Password: password"
echo "   Username: demo    | Password: demo123"
echo "   Username: guest   | Password: guest"
echo ""

# Start the application
echo "[INFO] Starting Chainlit app with authentication and chat history..."
echo "[INFO] App will be available at: http://localhost:8000"
echo ""

chainlit run chainlit_app.py -w --port 8000 