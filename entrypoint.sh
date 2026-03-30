#!/bin/bash

echo "--- Debug: Checking Environment ---"
echo "API_ID: ${TELEGRAM_API_ID}"

if [ -z "$TELEGRAM_API_ID" ] || [ -z "$TELEGRAM_API_HASH" ]; then
    echo "❌ ERROR: TELEGRAM_API_ID or TELEGRAM_API_HASH is empty!"
    exit 1
fi

mkdir -p /app/downloads/temp

echo "--- Starting Telegram Bot API Server ---"

/usr/local/bin/telegram-bot-api \
    --api-id=${TELEGRAM_API_ID} \
    --api-hash=${TELEGRAM_API_HASH} \
    --local \
    --dir=/app/downloads \
    --temp-dir=/app/downloads/temp \
    --log=/app/downloads/api_server.log \
    --verbosity=3 &

echo "Waiting for API server to bind to port 8081..."
MAX_RETRIES=15
COUNT=0
while ! curl -s http://localhost:8081 > /dev/null; do
    sleep 2
    COUNT=$((COUNT + 1))
    echo "Attempt $COUNT/$MAX_RETRIES..."
    if [ $COUNT -ge $MAX_RETRIES ]; then
        echo "❌ ERROR: Telegram API Server failed to start. Printing log:"
        cat /app/downloads/api_server.log
        exit 1
    fi
done

echo "✅ API Server is active!"
echo "--- Starting Python Bot ---"

export BOT_API_URL="http://localhost:8081"

exec python3 -m bot.main
