#!/bin/bash

# InkyPi Wingsurf Forecast Service Deployment Script
# ================================================

set -e

echo "InkyPi Wingsurf Forecast Service Deployment"
echo "==============================================="

# Check if we're in the right directory
if [[ ! -f "docker-compose.yml" && ! -f "Dockerfile" ]]; then
    echo "❌ Error: Please run this script from the wingsurf-forecast directory"
    exit 1
fi

# Create data directory if it doesn't exist
echo "Creating data directory..."
mkdir -p data

# Check if config file exists
if [[ ! -f "config/config.json" ]]; then
    echo "Warning: config/config.json not found. Using default configuration."
    echo "Please edit config/config.json to set your location and preferences."
fi

# Build and start the service
echo "🔨 Building and starting the service..."

# Check if docker compose (new) or docker-compose (legacy) is available
if command -v docker compose &> /dev/null; then
    DOCKER_COMPOSE="docker compose"
elif command -v docker-compose &> /dev/null; then
    DOCKER_COMPOSE="docker-compose"
else
    echo "❌ Error: Neither 'docker compose' nor 'docker-compose' command found"
    exit 1
fi

echo "Using: $DOCKER_COMPOSE"
$DOCKER_COMPOSE build
$DOCKER_COMPOSE up -d

# Wait for service to be ready
echo "Waiting for service to start..."
sleep 10

# Health check
echo "🔍 Checking service health..."
if curl -f http://localhost:5000/health > /dev/null 2>&1; then
    echo "✅ Service is running and healthy!"
    echo ""
    echo "Dashboard: https://wingsurf.felixmrak.com"
    echo "API Endpoint: https://wingsurf.felixmrak.com/api/inkypi/morning-report"
    echo ""
    echo "Next steps:"
    echo "1. Update config/config.json with your location coordinates"
    echo "2. Test the API: curl https://wingsurf.felixmrak.com/api/current-conditions"
    echo "3. Integrate with your InkyPi using the provided plugin code"
    echo ""
else
    echo "❌ Service health check failed. Check logs with: docker logs wingsurf-forecast"
    exit 1
fi

echo "🎉 Deployment complete!"
