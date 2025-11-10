#!/bin/bash
# Greedy Shark Health Check Script
# Run this manually or via cron to monitor the shark

echo "🦈 Greedy Shark Health Check"
echo "============================="
echo ""

# Check if containers are running
monitor_status=$(docker inspect -f '{{.State.Status}}' greedy-shark-monitor 2>/dev/null || echo "not found")
bot_status=$(docker inspect -f '{{.State.Status}}' greedy-shark-bot 2>/dev/null || echo "not found")

# Check health
monitor_health=$(docker inspect -f '{{.State.Health.Status}}' greedy-shark-monitor 2>/dev/null || echo "no healthcheck")
bot_health=$(docker inspect -f '{{.State.Health.Status}}' greedy-shark-bot 2>/dev/null || echo "no healthcheck")

# Check restart count
monitor_restarts=$(docker inspect -f '{{.RestartCount}}' greedy-shark-monitor 2>/dev/null || echo "0")
bot_restarts=$(docker inspect -f '{{.RestartCount}}' greedy-shark-bot 2>/dev/null || echo "0")

echo "Monitor:"
echo "  Status: $monitor_status"
echo "  Health: $monitor_health"
echo "  Restarts: $monitor_restarts"
echo ""

echo "Bot:"
echo "  Status: $bot_status"
echo "  Health: $bot_health"
echo "  Restarts: $bot_restarts"
echo ""

# Overall status
if [[ "$monitor_status" == "running" ]] && [[ "$bot_status" == "running" ]]; then
    if [[ "$monitor_health" == "healthy" ]] && [[ "$bot_health" == "healthy" ]]; then
        echo "✅ All systems operational!"
        exit 0
    else
        echo "⚠️  Containers running but health check failing"
        exit 1
    fi
else
    echo "❌ One or more containers not running!"
    exit 2
fi
